"""Dedicated English->Chinese encoder/decoder NMT. CPU INT8, sentence SSE.
Started and owned by Strata; listens only on loopback, with no GPU or LLM prompt.
"""
from __future__ import annotations

import argparse
import json
import queue
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import ctranslate2
import sentencepiece as spm

if __package__:
    from .translation_text import protect_text
else:
    from translation_text import protect_text


def technical_terms(source, translated):
    """Correct a small, explicit software glossary only when its source term occurs.
    These contextual substitutions never change user input or the original history.
    """
    terms = (
        (r"\bclosing brace\b", ("关闭的束缚", "关闭的框架", "关闭的束", "封边框"), "右花括号"),
        (r"\bgeneration lock\b", ("代数锁", "一代锁"), "生成锁"),
        (r"\btranslation worker\b", ("翻译工作者", "翻译工作人员", "翻译人员", "翻译员"), "翻译工作线程"),
        (r"\bkeyboard hook\b", ("键盘钩", "键盘勾", "键盘钉"), "键盘钩子"),
        (r"\bexclusive access\b", ("独家访问权限", "专有访问权限", "独家访问权"), "独占访问权"),
        (r"\bInterception\b", ("拦截驱动程序", "截取驱动程序", "拦截驱动器"), "Interception 驱动程序"),
    )
    for pattern, variants, target in terms:
        if re.search(pattern, source, re.IGNORECASE):
            for variant in variants:
                if variant == "键盘钩":
                    translated = re.sub(r"键盘钩(?!子)", target, translated)
                else:
                    translated = translated.replace(variant, target)
    if re.search(r"\b(keyboard|mouse|keystroke)\b", source, re.IGNORECASE) and not re.search(
            r"\b(crypto|cryptographic|secret|encryption|password)\b", source, re.IGNORECASE):
        translated = translated.replace("密钥", "按键")
    if re.search(r"\bpassed through unchanged\b", source, re.IGNORECASE):
        translated = translated.replace("通过不变", "原样透传")
    return translated


class Marian:
    def __init__(self, model, threads=4, workers=2, backend="marian", beam_size=1):
        model = Path(model)
        self.backend, self.beam_size = backend, beam_size
        if backend == "t5":
            self.source = self.target = spm.SentencePieceProcessor(model_file=str(model / "spiece.model"))
        else:
            self.source = spm.SentencePieceProcessor(model_file=str(model / "source.spm"))
            self.target = spm.SentencePieceProcessor(model_file=str(model / "target.spm"))
        self.translator = ctranslate2.Translator(str(model), device="cpu", compute_type="int8",
                                                intra_threads=threads, inter_threads=workers)

    def stream_batch(self, texts, send, stopped, max_tokens=256):
        # Buffer decoder tokens locally. Only a completed, checked sentence is
        # published; <unk> or a decoding loop can never flood the client stream.
        selected, sources, protected = [], [], []
        for index, text in enumerate(texts):
            plan = protect_text(text.strip())
            prefix = "translate to zh: " if self.backend == "t5" else ""
            ids = self.source.encode(prefix + plan.masked)
            prose = bool(re.search(r"[A-Za-z]{2,}", plan.prose)) and not re.search(r"[\u3400-\u9fff]", plan.prose)
            if not prose or self.source.unk_id() in ids or len(ids) > 512:
                send(("token", index, text if not prose and plan.literals else text.strip()))
                send(("done", index, None))
                continue
            selected.append(index)
            protected.append(plan)
            language = [">>cmn_Hans<<"] if self.backend == "marian" else []
            sources.append(language + [self.source.id_to_piece(i) for i in ids] + ["</s>"])
        if not sources:
            return
        tokens, done = [[] for _ in sources], [False for _ in sources]
        specials = {"</s>", "<s>", "<pad>"}
        # An input-sized ceiling stops short, unfamiliar technical fragments from
        # generating hundreds of unrelated tokens. On limit, preserve the source.
        limit = min(max_tokens, max(32, max(len(source) for source in sources) * 2 + 16))

        def finish(index, ended):
            if done[index] or stopped.is_set():
                return
            original = texts[selected[index]].strip()
            decoded = self.target.decode(tokens[index]).strip()
            bad = (not ended or not decoded or "⁇" in decoded or "\ufffd" in decoded
                   or bool(re.search(r"(.{1,24}?)\1{4,}", decoded, re.DOTALL)))
            restored = None if bad else protected[index].restore(
                technical_terms(protected[index].prose, decoded))
            send(("token", selected[index], original if restored is None else restored))
            send(("done", selected[index], None))
            done[index] = True

        def callback(step):
            if stopped.is_set():
                return True
            index = step.batch_id
            if step.token not in specials:
                tokens[index].append(step.token)
            if step.is_last:
                finish(index, step.token == "</s>")
            return False

        results = self.translator.translate_batch(sources, beam_size=self.beam_size, max_batch_size=0,
            max_input_length=0, max_decoding_length=limit, callback=callback if self.beam_size == 1 else None,
            return_end_token=True,
            disable_unk=True, no_repeat_ngram_size=3,
            return_scores=False, return_attention=False)
        if stopped.is_set():
            return
        for index, result in enumerate(results):
            if not done[index]:
                hypothesis = result.hypotheses[0]
                tokens[index] = [token for token in hypothesis if token not in specials]
                finish(index, bool(hypothesis) and hypothesis[-1] == "</s>")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        payload = json.dumps({"status":"ok", "backend":self.server.model.backend, "device":"cpu", "compute_type":"int8"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path != "/translate":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 131072:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length))
            texts = body.get("texts", [body.get("text", "")])
            if not isinstance(texts, list) or not 1 <= len(texts) <= 16 or not all(
                isinstance(text, str) and text.strip() and len(text) <= 8192 for text in texts):
                raise ValueError("expected 1..16 non-empty English segments")
            max_tokens = body.get("max_output_tokens", 256)
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 16 <= max_tokens <= 4096:
                raise ValueError("invalid max_output_tokens")
        except (ValueError, TypeError) as exc:
            self.send_error(400, str(exc))
            return
        messages = queue.SimpleQueue()
        stopped = threading.Event()
        def work():
            try:
                self.server.model.stream_batch(texts, messages.put, stopped, max_tokens)
            except Exception as exc:
                messages.put(("error", -1, str(exc)))
            finally:
                messages.put(("end", -1, None))
        threading.Thread(target=work, name="marian-request", daemon=True).start()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        current = 0
        held = [[] for _ in texts]
        completed = [False for _ in texts]
        def send(item):
            self.wfile.write(b"data: " + json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\n\n")
            self.wfile.flush()
        try:
            while True:
                kind, index, value = messages.get()
                if kind == "error":
                    send({"error":value})
                    break
                if kind == "end":
                    break
                if kind == "token":
                    held[index].append(value)
                elif kind == "done":
                    completed[index] = True
                while current < len(texts):
                    for value in held[current]:
                        send({"index":current, "content":value, "stop":False})
                    held[current].clear()
                    if not completed[current]:
                        break
                    send({"index":current, "content":"", "stop":True})
                    current += 1
        except (OSError, ValueError):
            pass
        finally:
            stopped.set()


class Server(ThreadingHTTPServer):
    model: Marian
    daemon_threads = True
    allow_reuse_address = False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int, default=8096)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--backend", choices=("marian", "t5"), default="marian")
    ap.add_argument("--beam-size", type=int, choices=(1,2,3,4), default=1)
    args = ap.parse_args()
    model = Marian(args.model, args.threads, args.workers, args.backend, args.beam_size)
    with Server(("127.0.0.1", args.port), Handler) as httpd:
        httpd.model = model
        print(f"{args.backend} ready: CPU {model.translator.compute_type}, beam {args.beam_size}, {args.threads} threads x {args.workers} workers, port {args.port}", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
