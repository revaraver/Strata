"""CPU translation's stream ordering, hot config, cancellation and all three APIs.
Run: python -m unittest serve.test_reasoning_translation -v (no GPU/downloads).
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from serve.frontend import ChatTemplate, Event, ToolCall
from serve.reasoning_translation import DEFAULTS, TranslationManager, load_config, take_segment
from serve.server import ByteTokenizer, MockEngine, Service, serve

ROOT = Path(__file__).resolve().parents[1]


class Translation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "translation.json"
        self.configure(enabled=True, auto_start=False)
        self.manager = TranslationManager(self.path)
        self.cancel = threading.Event()

    def tearDown(self):
        self.manager.close()
        self.tmp.cleanup()

    def configure(self, **values):
        self.path.write_text(json.dumps({"backend": "marian", "sentence_mode": False, "batch_segments": 1, **values}), encoding="utf-8")

    def run_stream(self, events):
        return list(self.manager.wrap(iter(events), self.cancel))

    def test_off_is_exact_passthrough(self):
        self.configure(enabled=False)
        items = [("event", Event("reasoning", "English")), ("event", Event("content", "Answer")), ("done", {"finish": "stop"})]
        self.assertEqual(self.run_stream(items), items)

    def test_hot_toggle_is_seen_by_next_request(self):
        self.configure(enabled=False)
        with mock.patch.object(self.manager, "translate", return_value=iter(["中文"])):
            self.assertEqual(self.run_stream([("event", Event("reasoning", "English"))])[0][1].text, "English")
            self.configure(enabled=True, auto_start=False)
            self.assertEqual(self.run_stream([("event", Event("reasoning", "English"))])[0][1].text, "中文")

    def test_sentence_tail_tool_and_done_order(self):
        seen = []
        def translate(text, cfg, stop):
            seen.append(text)
            yield "中"
            yield "文"
        call = Event("tool_start", call=ToolCall("f", {}))
        done = {"finish": "stop", "completion_tokens": 17}
        with mock.patch.object(self.manager, "translate", side_effect=translate):
            result = self.run_stream([("event", Event("reasoning", "First. Second tail")), ("event", call), ("done", done)])
        self.assertEqual("".join(seen), "First. Second tail")
        self.assertEqual([x.text for k, x in result if k == "event" and x.kind == "reasoning"], ["中", "文", "中", "文"])
        self.assertIs(result[-2][1], call)
        self.assertIs(result[-1][1], done)

    def test_translation_is_emitted_before_the_segment_finishes(self):
        release = threading.Event()
        def translate(text, cfg, stop):
            yield "先"
            release.wait(2)
            yield "后"
        with mock.patch.object(self.manager, "translate", side_effect=translate):
            stream = self.manager.wrap(iter([("event", Event("reasoning", "Hello")), ("done", {})]), self.cancel)
            first = next(stream)
            self.assertEqual(first[1].text, "先")
            release.set()
            self.assertEqual([x.text for k, x in stream if k == "event"], ["后"])

    def test_failure_returns_original_once_and_skips_remaining_translations(self):
        with mock.patch.object(self.manager, "translate", side_effect=OSError("offline")) as translation, contextlib.redirect_stdout(io.StringIO()):
            result = self.run_stream([("event", Event("reasoning", "First. Second. Final")), ("done", {})])
        self.assertEqual("".join(x.text for k, x in result if k == "event"), "First. Second. Final")
        self.assertEqual(translation.call_count, 1)

    def test_partial_failure_labels_original_and_keeps_the_answer(self):
        def translate(text, cfg, stop):
            yield "部分"
            raise OSError("interrupted")
        with mock.patch.object(self.manager, "translate", side_effect=translate), contextlib.redirect_stdout(io.StringIO()):
            result = self.run_stream([("event", Event("reasoning", "English")), ("event", Event("content", "Answer")), ("done", {})])
        self.assertIn("原文：", "".join(x.text for k, x in result if k == "event"))
        self.assertEqual(result[-2][1].text, "Answer")

    def test_cancellation_does_not_wait_for_cpu_inference(self):
        closed, started = threading.Event(), threading.Event()
        def source():
            try:
                yield "event", Event("reasoning", "Hello")
                yield "done", {}
            finally:
                closed.set()
        def translate(text, cfg, stop):
            started.set()
            stop.wait(3)
            return
            yield "unused"
        def cancel():
            started.wait(2)
            self.cancel.set()
        threading.Thread(target=cancel, daemon=True).start()
        begin = time.monotonic()
        with mock.patch.object(self.manager, "translate", side_effect=translate):
            self.assertEqual(list(self.manager.wrap(source(), self.cancel)), [])
        self.assertLess(time.monotonic() - begin, 1)
        self.assertTrue(closed.is_set())
        self.assertEqual(self.manager.active, 0)

    def test_segmentation_keeps_decimal_and_long_tail_without_loss(self):
        text = "Value 3.14 is valid. " + "a word " * 120
        left, remaining = take_segment(text, DEFAULTS, 0)
        self.assertEqual(left, "Value 3.14 is valid.")
        pieces = [left]
        while remaining:
            part, remaining = take_segment(remaining, DEFAULTS, 0, final=True)
            self.assertTrue(part)
            pieces.append(part)
        self.assertEqual("".join(pieces), text)

    def test_invalid_configuration_keeps_the_last_good_settings(self):
        self.manager.refresh()
        self.configure(enabled="false")
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.refresh()
        self.assertTrue(self.manager.cfg["enabled"])
        with self.assertRaises(ValueError):
            load_config(self.path)

    def test_cpu_child_flags_and_loopback_are_enforced(self):
        model = self.path.parent / DEFAULTS["model"]
        model.mkdir(parents=True)
        (model / "model.bin").touch()
        self.configure(enabled=True)
        proc = mock.Mock()
        proc.poll.return_value = None
        with mock.patch("serve.reasoning_translation.subprocess.Popen", return_value=proc) as popen, mock.patch("serve.reasoning_translation.contain"), mock.patch("serve.reasoning_translation.socket.socket") as probe, contextlib.redirect_stdout(io.StringIO()):
            probe.return_value.__enter__.return_value.connect_ex.return_value = 111
            self.manager.refresh()
        args = popen.call_args.args[0]
        self.assertTrue(any(arg.endswith("nmt_translation.py") for arg in args))
        self.assertEqual(args[args.index("--port") + 1], str(DEFAULTS["port"]))
        self.assertEqual(args[args.index("--threads") + 1], str(DEFAULTS["threads"]))
        self.assertEqual(popen.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"], "")
        self.configure(enabled=False)
        self.manager.refresh()
        proc.terminate.assert_called_once()

    def test_wire_sse_is_incremental_and_preserves_surrounding_whitespace(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.request = body
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                for item in ({"index":0, "content": "中", "stop": False}, {"index":0, "content": "文", "stop": False}, {"index":0, "content": "", "stop": True}):
                    self.wfile.write(("data: " + json.dumps(item) + "\n\n").encode()); self.wfile.flush()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        cfg = {**DEFAULTS, "auto_start": False, "port": httpd.server_address[1]}
        try:
            self.assertEqual(list(self.manager.translate(" \nEnglish\n", cfg, threading.Event())), [" \n", "中", "文", "\n"])
            self.assertEqual(httpd.request["texts"], [" \nEnglish\n"])
        finally:
            httpd.shutdown(); httpd.server_close()


class APIs(unittest.TestCase):
    def test_all_apis_translate_only_reasoning_and_keep_usage(self):
        tok = ByteTokenizer()
        svc = Service(MockEngine(tok, "English thought.</think>Answer."), tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translation.json"
            path.write_text('{"enabled": true, "auto_start": false}', encoding="utf-8")
            manager = svc.reasoning_translation = TranslationManager(path)
            httpd = serve(svc, port=0)
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            def translate(text, cfg, stop):
                yield "中文"
                yield "思考。"
            try:
                with mock.patch.object(manager, "translate", side_effect=translate):
                    for api in ("chat/completions", "messages", "responses"):
                        for streaming in (False, True):
                            body = {"model": "m", "stream": streaming, "max_tokens": 100}
                            if api == "responses":
                                body.update(input="hello", reasoning={"summary": "detailed"})
                            else:
                                body["messages"] = [{"role": "user", "content": "hello"}]
                            request = urllib.request.Request(base + "/v1/" + api, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
                            with self.subTest(api=api, stream=streaming), urllib.request.urlopen(request, timeout=10) as response:
                                raw = response.read().decode()
                                if streaming:
                                    events = [json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:") and line[5:].strip() != "[DONE]"]
                                    raw = json.dumps(events, ensure_ascii=False)
                                self.assertNotIn("English thought", raw)
                                self.assertIn("中文", raw)
                                if not streaming:
                                    self.assertIn("Answer.", raw)
                                    result = json.loads(raw)
                                    if api == "chat/completions":
                                        self.assertEqual(result["choices"][0]["message"]["content"], "Answer.")
                                        # ByteTokenizer also counts the generated stop token.
                                        self.assertEqual(result["usage"]["completion_tokens"], len("English thought.</think>Answer.") + 1)
                                    elif api == "messages":
                                        self.assertEqual(result["content"][-1]["text"], "Answer.")
            finally:
                httpd.shutdown(); httpd.server_close(); manager.close()


if __name__ == "__main__":
    unittest.main()
