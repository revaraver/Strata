"""Optional CPU translation of the *outgoing* reasoning stream.

The native engine, prompts, MCP conversation history and token counts retain the
original text. Only the API adapters see translated Events. Configuration is a
separate JSON file, watched while Strata runs; children share its Windows job.
"""
from __future__ import annotations

import atexit
import json
import math
import os
import queue
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from http.client import HTTPException

from serve.frontend import Event
from serve.reasoning_history import ReasoningHistory
from serve.translation_text import has_english_prose, protected_spans
from serve.winjob import contain

DEFAULTS = {
    "enabled": False,
    "auto_start": True,
    "backend": "t5",
    "sentence_mode": True,
    "model": "pack/translation/t5-en-zh-small-ct2",
    "port": 8096,
    "threads": 8,
    "beam_size": 1,
    "nmt_workers": 2,
    "batch_segments": 8,
    "min_segment_chars": 24,
    "segment_chars": 128,
    "flush_interval_s": 0.12,
    "timeout_s": 10,
    "startup_timeout_s": 30,
    "max_pending_segments": 32,
    "max_output_tokens": 256,
}
PROCESS_KEYS = ("backend", "model", "port", "threads", "nmt_workers", "beam_size")
SENTENCE = re.compile(r'[.!?](?:["\')\]]*)(?=\s)|[。！？](?:["\'”’）\]]*)|\n+')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback never uses a system proxy


def background_events(source, cancel, enter=None, leave=None):
    """Read native events on their own thread. Queue.put never waits for translation
    or the client. The backlog is limited by the request's native context/output
    budget. Only this producer owns and closes the native generator."""
    events = queue.SimpleQueue()

    def produce():
        try:
            if enter is not None:
                enter()
            for item in source:
                if cancel.is_set():
                    break
                events.put(("item", item))
        except Exception as exc:
            events.put(("error", exc))
        finally:
            try:
                if hasattr(source, "close"):
                    source.close()
                if leave is not None:
                    leave()
            finally:
                events.put(("end", None))

    thread = threading.Thread(target=produce, name="strata-native-generation", daemon=True)
    thread.start()  # eager: even translator configuration/startup cannot hold the native producer

    def consume():
        ended = False
        try:
            while not cancel.is_set():
                try:
                    kind, item = events.get(timeout=0.1)
                except queue.Empty:
                    yield "ping", None
                    continue
                if kind == "end":
                    ended = True
                    return
                if kind == "error":
                    raise item
                yield item
        finally:
            if not ended:
                cancel.set()
    return consume()


def load_config(path: Path) -> dict:
    if not path.exists():
        return dict(DEFAULTS)
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("translation config must be a JSON object")
    unknown = value.keys() - DEFAULTS.keys()
    if unknown:
        raise ValueError(f"unknown translation settings: {', '.join(sorted(unknown))}")
    cfg = {**DEFAULTS, **value}
    if cfg["backend"] not in ("marian", "t5"):
        raise ValueError("backend: expected marian or t5 (CPU encoder/decoder NMT)")
    for name in ("enabled", "auto_start", "sentence_mode"):
        if not isinstance(cfg[name], bool):
            raise ValueError(f"{name}: expected true or false")
    for name, lo, hi in (("port", 1, 65535), ("threads", 1, 256), ("beam_size", 1, 4), ("segment_chars", 24, 2000),
                         ("min_segment_chars", 8, 256), ("nmt_workers", 1, 16), ("batch_segments", 1, 16),
                         ("max_pending_segments", 1, 32), ("max_output_tokens", 64, 4096)):
        n = cfg[name]
        if isinstance(n, bool) or not isinstance(n, int) or not lo <= n <= hi:
            raise ValueError(f"{name}: expected integer {lo}..{hi}")
    for name in ("flush_interval_s", "timeout_s", "startup_timeout_s"):
        n = cfg[name]
        if isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n) or not 0 < n <= 600:
            raise ValueError(f"{name}: expected seconds > 0 and <= 600")
    for name in ("model",):
        if not isinstance(cfg[name], str) or not cfg[name].strip():
            raise ValueError(f"{name}: expected a file path")
    return cfg


def needs_translation(text: str) -> bool:
    # Literal code bypasses the NMT service; Chinese prose is already translated.
    return has_english_prose(text)


def take_segment(text: str, cfg: dict, elapsed: float, final: bool = False) -> tuple[str, str]:
    """Split prose at sentence boundaries while keeping streamed code opaque."""
    if not text:
        return "", ""
    spans = protected_spans(text, final=final)
    boundaries = [match.end() for match in SENTENCE.finditer(text)
                  if not any(span.start < match.end() and span.end > match.start()
                             for span in spans)]
    for span in spans:
        if span.block:
            if span.start:
                boundaries.append(span.start)
            if span.complete or final:
                boundaries.append(span.end)
    if cfg.get("sentence_mode", True):
        if boundaries:
            end = min(boundaries)
            return text[:end], text[end:]
        return (text, "") if final else ("", text)
    limit = cfg["segment_chars"]
    boundary = min((end for end in boundaries if end <= limit), default=None)
    if boundary is not None:
        n = boundary
    elif len(text) >= limit:
        n = text.rfind(" ", 0, limit + 1)
        if n < 8:
            n = limit
    elif elapsed >= cfg["flush_interval_s"] and len(text) >= cfg["min_segment_chars"]:
        n = text.rfind(" ")
        if n < 8:
            return (text, "") if final else ("", text)
    elif final:
        n = len(text)
    else:
        return "", text
    for span in spans:
        if span.start < n < span.end:
            if span.start:
                n = span.start
            elif span.complete or final:
                n = span.end
            else:
                return "", text
            break
    return text[:n], text[n:]


class TranslationManager:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.lock = threading.RLock()
        self.cfg = dict(DEFAULTS)
        self.proc = None
        self.signature = None
        self.active = 0
        self.stopped = threading.Event()
        self.last_start = 0.0
        self.last_error = None
        self.thread = None
        self.history = ReasoningHistory(self.path.with_suffix(".history.sqlite"))

    def start(self):
        self.refresh()
        self.thread = threading.Thread(target=self._watch, name="strata-translation-config", daemon=True)
        self.thread.start()
        atexit.register(self.close)

    def _watch(self):
        while not self.stopped.wait(1):
            self.refresh()

    def refresh(self):
        try:
            cfg = load_config(self.path)
            with self.lock:
                self.cfg = cfg
                sig = tuple(cfg[k] for k in PROCESS_KEYS)
                if self.active:
                    # Process settings take effect once existing replies finish. New
                    # replies must still address the running child's port, not a new one.
                    if self.signature is not None and sig != self.signature:
                        for key, value in zip(PROCESS_KEYS, self.signature):
                            self.cfg[key] = value
                    return  # current replies finish with the settings they started with
                if not cfg["enabled"] or not cfg["auto_start"]:
                    self._stop_child()
                    return
                if self.proc is not None and self.proc.poll() is None and self.signature == sig:
                    return
                self._stop_child()
                if time.monotonic() - self.last_start < 5:
                    return
                self.last_start = time.monotonic()
                self._start_child(cfg)
                self.signature = sig
                self.last_error = None
        except (OSError, ValueError, RuntimeError) as exc:
            message = str(exc)
            if message != self.last_error:
                print(f"[strata translation] {message}; reasoning falls back to its original text", flush=True)
                self.last_error = message

    def _start_child(self, cfg):
        root = self.path.parent
        model = Path(cfg["model"])
        model = model if model.is_absolute() else root / model
        if not (model / "model.bin").is_file():
            raise RuntimeError("NMT model missing; run tools/setup_nmt_translation.py")
        args = [sys.executable, "-X", "utf8", str(Path(__file__).with_name("nmt_translation.py")),
                "--model", str(model), "--port", str(cfg["port"]), "--threads", str(cfg["threads"]),
                "--workers", str(cfg["nmt_workers"]), "--backend", cfg["backend"], "--beam-size", str(cfg["beam_size"])]
        label = f"{cfg['backend']} NMT INT8"
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", cfg["port"])) == 0:
                raise RuntimeError(f"translation port {cfg['port']} is already occupied")
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_")}
        env.update(CUDA_VISIBLE_DEVICES="", HIP_VISIBLE_DEVICES="", ROCR_VISIBLE_DEVICES="")
        with self.path.with_suffix(".log").open("ab") as out:
            self.proc = subprocess.Popen(args, cwd=str(root), env=env, stdout=out, stderr=subprocess.STDOUT,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        contain(self.proc)
        print(f"[strata translation] CPU {label} starting on 127.0.0.1:{cfg['port']} "
              f"({cfg['threads']} threads); clients keep the original Strata port", flush=True)

    def _stop_child(self):
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            if os.name == "nt":
                # Windows' venv python.exe is a launcher with a real Python child.
                # Terminating just the launcher leaves the service/port alive.
                try:
                    killed = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], timeout=5,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if killed.returncode == 0:
                        proc.wait(timeout=3)
                        return
                except (OSError, subprocess.TimeoutExpired):
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

    def close(self):
        self.stopped.set()
        with self.lock:
            self._stop_child()
        self.history.close()

    def restore_messages(self, messages):
        try:
            return self.history.messages(messages)
        except (OSError, sqlite3.Error) as exc:
            print(f"[strata translation] could not restore reasoning history: {exc}", flush=True)
            return messages

    def _remember(self, original, display):
        try:
            self.history.remember("".join(original), "".join(display))
        except (OSError, sqlite3.Error) as exc:
            print(f"[strata translation] could not preserve reasoning history: {exc}", flush=True)

    def status(self):
        with self.lock:
            return {"enabled": self.cfg["enabled"], "backend": self.cfg["backend"], "active": self.active, "native_generation_async": True}

    def _ready(self, cfg, stop):
        deadline = time.monotonic() + cfg["startup_timeout_s"]
        base = f"http://127.0.0.1:{cfg['port']}"
        while not stop.is_set():
            if cfg["auto_start"]:
                with self.lock:
                    if self.proc is None or self.proc.poll() is not None:
                        raise RuntimeError("CPU translation process is unavailable; see reasoning-translation.log")
            try:
                with OPENER.open(base + "/health", timeout=1) as response:
                    if response.status == 200:
                        return base
            except (OSError, urllib.error.URLError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("CPU translation startup timed out")
            stop.wait(0.1)
        return None

    def translate_many(self, texts, cfg, stop):
        """NMT microbatch, completed sentences emitted in their original order."""
        selected = [i for i, text in enumerate(texts) if needs_translation(texts[i])]
        if not selected:
            for i, text in enumerate(texts):
                yield i, text
                yield i, None
            return
        base = self._ready(cfg, stop)
        if base is None or stop.is_set():
            return
        request = urllib.request.Request(base + "/translate", data=json.dumps({
            "texts": [texts[i] for i in selected], "max_output_tokens": cfg["max_output_tokens"]
        }).encode("utf-8"), headers={"Content-Type": "application/json"})
        cursor = 0
        emitted = set()
        with OPENER.open(request, timeout=cfg["timeout_s"]) as response:
            for line in response:
                if stop.is_set():
                    return
                if not line.startswith(b"data:"):
                    continue
                item = json.loads(line[5:])
                if not isinstance(item, dict) or item.get("error"):
                    raise RuntimeError("NMT translation stream failed")
                index = item.get("index")
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(selected):
                    raise ValueError("invalid NMT segment index")
                i = selected[index]
                while cursor < i:
                    if cursor in selected:
                        raise RuntimeError("NMT segment ended out of order")
                    yield cursor, texts[cursor]
                    yield cursor, None
                    cursor += 1
                text = texts[i]
                chunk = item.get("content", "")
                if chunk:
                    if i not in emitted:
                        yield i, text[:len(text) - len(text.lstrip())]
                        emitted.add(i)
                    yield i, chunk
                if item.get("stop"):
                    if i not in emitted:
                        raise RuntimeError("empty NMT translation")
                    trailing = text[len(text.rstrip()):]
                    if trailing:
                        yield i, trailing
                    yield i, None
                    cursor = i + 1
        while cursor < len(texts):
            if cursor in selected:
                raise RuntimeError("incomplete NMT stream")
            yield cursor, texts[cursor]
            yield cursor, None
            cursor += 1

    def translate(self, text: str, cfg: dict, stop):
        for index, chunk in self.translate_many([text], cfg, stop):
            if chunk is not None:
                yield chunk

    def wrap(self, source, cancel):
        """Translate the independent native-event backlog. Translation/client queues
        can wait here; they never stop the native producer. Preserve API event order
        and original English for reasoning returned on later requests."""
        self.refresh()
        with self.lock:
            cfg = dict(self.cfg)
            if cfg["enabled"]:
                self.active += 1
        if not cfg["enabled"]:
            yield from source
            return
        inputs = queue.Queue(maxsize=cfg["max_pending_segments"])
        outputs = queue.Queue(maxsize=128)
        stop = threading.Event()
        pending, buffer, since = 0, "", time.monotonic()
        raw_block, display_block, raw_all, display_all = [], [], [], []

        def put(item):
            while not stop.is_set() and not cancel.is_set():
                try:
                    outputs.put(item, timeout=0.1)
                    return
                except queue.Full:
                    pass

        def worker():
            failed = False
            while not stop.is_set() and not cancel.is_set():
                try:
                    texts = [inputs.get(timeout=0.1)]
                except queue.Empty:
                    continue
                if not failed:
                    # Batch only segments already waiting: never delay the first
                    # available segment to fill a batch.
                    while len(texts) < cfg["batch_segments"]:
                        try:
                            texts.append(inputs.get_nowait())
                        except queue.Empty:
                            break
                emitted = [False] * len(texts)
                completed = [False] * len(texts)
                sentence_chunks = [[] for _ in texts]

                def publish_sentence(i):
                    translated = "".join(sentence_chunks[i]) or texts[i]
                    put(("event", Event("reasoning", translated, original_text=texts[i])))
                    emitted[i] = True

                try:
                    if failed:
                        put(("event", Event("reasoning", texts[0], original_text=texts[0])))
                        emitted[0] = True
                    else:
                        if len(texts) == 1:
                            chunks = ((0, chunk) for chunk in self.translate(texts[0], cfg, stop))
                        else:
                            chunks = self.translate_many(texts, cfg, stop)
                        for i, chunk in chunks:
                            if chunk is None:
                                if cfg["sentence_mode"]:
                                    publish_sentence(i)
                                completed[i] = True
                                put(("translated", None))
                            elif chunk:
                                if cfg["sentence_mode"]:
                                    sentence_chunks[i].append(chunk)
                                    continue
                                original = texts[i] if not emitted[i] else ""
                                emitted[i] = True
                                put(("event", Event("reasoning", chunk, original_text=original)))
                except (OSError, ValueError, RuntimeError, HTTPException) as exc:
                    failed = True
                    print(f"[strata translation] {exc}; using original reasoning for the rest of this reply", flush=True)
                    for i, text in enumerate(texts):
                        if not completed[i]:
                            prefix = "\n[翻译中断，原文：] " if emitted[i] else ""
                            put(("event", Event("reasoning", prefix + text, original_text="" if emitted[i] else text)))
                            completed[i] = True
                            put(("translated", None))
                finally:
                    for i in range(len(texts)):
                        if not completed[i]:
                            if cfg["sentence_mode"] and not emitted[i] and not stop.is_set() and not cancel.is_set():
                                publish_sentence(i)
                            put(("translated", None))

        thread = threading.Thread(target=worker, name="strata-reasoning-translator", daemon=True)
        thread.start()

        def drain(wait=False):
            nonlocal pending
            heartbeat = time.monotonic()
            while pending and not cancel.is_set():
                try:
                    item = outputs.get(timeout=0.1) if wait else outputs.get_nowait()
                except queue.Empty:
                    if not wait:
                        return
                    if time.monotonic() - heartbeat >= 1:
                        yield "ping", None
                        heartbeat = time.monotonic()
                    continue
                if item[0] == "translated":
                    pending -= 1
                else:
                    display_block.append(item[1].text)
                    display_all.append(item[1].text)
                    yield item

        def submit(text):
            nonlocal pending
            if not text:
                return
            # Limit outstanding chunks, including the currently translating chunk.
            while pending >= cfg["max_pending_segments"] and not cancel.is_set():
                yield from drain(wait=True)
            if not cancel.is_set():
                inputs.put_nowait(text)
                pending += 1

        try:
            for kind, item in source:
                if cancel.is_set():
                    return
                yield from drain()
                if kind == "event" and item.kind == "reasoning":
                    buffer += item.text or ""
                    raw_block.append(item.text or "")
                    raw_all.append(item.text or "")
                    while True:
                        part, buffer = take_segment(buffer, cfg, time.monotonic() - since)
                        if not part:
                            break
                        yield from submit(part)
                        since = time.monotonic()
                    continue
                if kind == "ping":
                    part, buffer = take_segment(buffer, cfg, time.monotonic() - since)
                    if part:
                        yield from submit(part)
                        since = time.monotonic()
                    yield kind, item
                    continue
                while buffer:
                    part, buffer = take_segment(buffer, cfg, 0, final=True)
                    yield from submit(part)
                yield from drain(wait=True)
                if cancel.is_set():
                    return
                self._remember(raw_block, display_block)
                raw_block.clear()
                display_block.clear()
                if kind == "done":
                    self._remember(raw_all, display_all)
                yield kind, item
            while buffer:
                part, buffer = take_segment(buffer, cfg, 0, final=True)
                yield from submit(part)
            yield from drain(wait=True)
            if not cancel.is_set():
                self._remember(raw_block, display_block)
                self._remember(raw_all, display_all)
        finally:
            stop.set()
            if hasattr(source, "close"):
                source.close()
            with self.lock:
                self.active -= 1
