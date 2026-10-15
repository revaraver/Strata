"""Opt-in Windows CPU/model smoke test; no GPU engine is started.
Run after setup_reasoning_translation.py. Tests real streamed translations through
all three Strata APIs, and the translator's startup/shutdown and live JSON switch.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.frontend import ChatTemplate
from serve.reasoning_translation import DEFAULTS, TranslationManager
from serve.server import ByteTokenizer, MockEngine, Service, serve


def main():
    directory = ROOT / "local-upgrade"
    directory.mkdir(exist_ok=True)
    path = directory / "translation-smoke-config.json"
    cfg = {**DEFAULTS, "enabled": True, "port": 8097,
           "model": str(ROOT / DEFAULTS["model"])}
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    manager = TranslationManager(path)
    report = {"model": cfg["model"], "device": "CPU", "samples": [], "apis": []}
    httpd = None
    try:
        manager.start()
        started = time.monotonic()
        manager._ready(cfg, threading.Event())
        report["startup_s"] = round(time.monotonic() - started, 3)
        print(f"CPU translator ready after {report['startup_s']}s", flush=True)
        samples = [
            "I need to check whether the file exists before reading its contents.",
            "The user wants the reasoning translated into Chinese without changing the model's final answer.",
            "We should preserve the API endpoint and run the translation model on the CPU to avoid using GPU memory.",
        ]
        for text in samples:
            chunks, first, started = [], None, time.monotonic()
            for chunk in manager.translate(text, cfg, threading.Event()):
                if chunk.strip() and first is None:
                    first = time.monotonic() - started
                chunks.append(chunk)
            result = {"en": text, "zh": "".join(chunks), "first_chunk_s": round(first or 0, 3),
                      "total_s": round(time.monotonic() - started, 3), "chunks": len(chunks)}
            assert any("\u4e00" <= c <= "\u9fff" for c in result["zh"]), result
            assert result["chunks"] > 1, result
            report["samples"].append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        tok = ByteTokenizer()
        svc = Service(MockEngine(tok, samples[0] + "</think>The final answer stays unchanged."), tok,
                      ChatTemplate(ROOT / "serve/chat_template.jinja"))
        svc.reasoning_translation = manager
        httpd = serve(svc, port=0)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        for api in ("chat/completions", "messages", "responses"):
            body = {"model": "m", "stream": True, "max_tokens": 256}
            if api == "responses":
                body.update(input="hello", reasoning={"summary": "detailed"})
            else:
                body["messages"] = [{"role": "user", "content": "hello"}]
            request = urllib.request.Request(base + "/v1/" + api, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            events, reasoning, content, first, started = [], [], [], None, time.monotonic()
            native_idle_at_first_translation = None
            with urllib.request.urlopen(request, timeout=90) as response:
                for line in response:
                    if not line.startswith(b"data:") or line[5:].strip() == b"[DONE]":
                        continue
                    event = json.loads(line[5:])
                    events.append(event)
                    if api == "chat/completions":
                        delta = event["choices"][0]["delta"]
                        r, c = delta.get("reasoning_content", ""), delta.get("content", "")
                    elif api == "messages":
                        delta = event.get("delta", {})
                        r, c = delta.get("thinking", ""), delta.get("text", "")
                    else:
                        typ = event.get("type")
                        r = event.get("delta", "") if typ in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta") else ""
                        c = event.get("delta", "") if typ == "response.output_text.delta" else ""
                    if r:
                        if first is None:
                            first = time.monotonic() - started
                            native_idle_at_first_translation = svc.metrics()["live"]["state"] == "idle"
                        reasoning.append(r)
                    content.append(c)
            assert len(reasoning) >= 1, (api, events)
            assert "I need to check" not in "".join(reasoning), reasoning
            assert "".join(content) == "The final answer stays unchanged.", content
            result = {"api": api, "first_reasoning_s": round(first or 0, 3), "total_s": round(time.monotonic() - started, 3),
                      "reasoning_chunks": len(reasoning), "zh": "".join(reasoning),
                      "native_idle_at_first_translation": native_idle_at_first_translation}
            assert native_idle_at_first_translation, "native generation waited for translation"
            report["apis"].append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        old_pid = manager.proc.pid
        cfg["enabled"] = False
        path.write_text(json.dumps(cfg), encoding="utf-8")
        deadline = time.monotonic() + 5
        while manager.proc is not None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert manager.proc is None, "config watcher did not stop the child"
        cfg["enabled"] = True
        path.write_text(json.dumps(cfg), encoding="utf-8")
        deadline = time.monotonic() + 8
        while manager.proc is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert manager.proc is not None and manager.proc.pid != old_pid, "config watcher did not start a new child"
        report["hot_toggle"] = "passed"
        print("Live JSON off/on: passed", flush=True)
    finally:
        if httpd:
            httpd.shutdown(); httpd.server_close()
        manager.close()
    report["shutdown"] = "passed"
    (directory / "translation-smoke-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Live CPU/API translation smoke test passed; child stopped.", flush=True)


if __name__ == "__main__":
    main()
