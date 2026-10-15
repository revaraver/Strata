"""Regression tests: slow translation must not stop native generation or change history."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from serve import responses
from serve.frontend import ChatTemplate, Event, anthropic_to_messages, openai_to_messages
from serve.reasoning_translation import TranslationManager
from serve.server import ByteTokenizer, MockEngine, Service

ROOT = Path(__file__).resolve().parents[1]


class AsyncGeneration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "translation.json"
        self.path.write_text('{"enabled":true,"auto_start":false,"max_pending_segments":1}', encoding="utf-8")
        self.manager = TranslationManager(self.path)
        self.tok = ByteTokenizer()
        self.svc = Service(MockEngine(self.tok, "English thought.</think>Answer."), self.tok,
                           ChatTemplate(ROOT / "serve/chat_template.jinja"))
        self.svc.reasoning_translation = self.manager

    def tearDown(self):
        self.manager.close()
        self.tmp.cleanup()

    def test_native_generation_and_fifo_finish_while_translation_is_blocked(self):
        release, translating, native_finished = threading.Event(), threading.Event(), threading.Event()
        script = " ".join(f"English step number {i}." for i in range(30)) + "</think>Answer."
        class Engine(MockEngine):
            def generate(engine, *args, **kwargs):
                try:
                    yield from super().generate(*args, **kwargs)
                finally:
                    native_finished.set()
        self.svc.engine = Engine(self.tok, script)
        def translate(text, cfg, stop):
            translating.set()
            release.wait(3)
            yield "中文。"
        result = []
        cancel = threading.Event()
        with mock.patch.object(self.manager, "translate", side_effect=translate):
            reader = threading.Thread(target=lambda: result.extend(self.svc.outgoing(
                self.svc.run([1], True, None, 2000, {}, cancel), cancel)))
            reader.start()
            try:
                self.assertTrue(translating.wait(1))
                self.assertTrue(native_finished.wait(1), "native generation waited for translation")
                self.assertFalse(release.is_set())
                self.assertTrue(self.svc.fifo.acquire(timeout=1), "translation retained the native FIFO")
                self.svc.fifo.release()
            finally:
                release.set()
                reader.join(3)
        self.assertFalse(reader.is_alive())
        self.assertEqual(result[-1][0], "done")

    def test_request_local_image_and_trace_are_transferred(self):
        trace = {"request": "one"}
        path = Path(self.tmp.name) / "embeddings.bin"
        path.touch()
        self.svc.embeddings.path = str(path)
        self.svc.request_trace.record = trace
        seen = []
        def source():
            seen.append((self.svc.embeddings.path, self.svc.request_trace.record))
            yield "event", Event("reasoning", "English")
            yield "done", {}
        with mock.patch.object(self.manager, "translate", return_value=iter(["中文"])):
            list(self.svc.outgoing(source(), threading.Event()))
        self.assertEqual(seen, [(str(path), trace)])
        self.assertIsNone(self.svc.embeddings.path)
        self.assertFalse(path.exists())

    def test_originals_restore_all_three_api_histories_and_survive_restart(self):
        original = "Original English reasoning."
        with mock.patch.object(self.manager, "translate", return_value=iter(["中文思考。"] )):
            list(self.svc.outgoing(iter([("event", Event("reasoning", original)), ("done", {})]), threading.Event()))
        self.manager.close()
        self.manager = self.svc.reasoning_translation = TranslationManager(self.path)
        requests = [
            openai_to_messages({"messages": [{"role":"assistant", "content":"Answer.", "reasoning_content":"中文思考。"}]} )[0],
            anthropic_to_messages({"messages": [{"role":"assistant", "content":[{"type":"thinking","thinking":"中文思考。"},{"type":"text","text":"Answer."}]}]} )[0],
            responses.input_messages({"input":[{"type":"reasoning","content":[{"type":"reasoning_text","text":"中文思考。"}]},{"role":"assistant","content":"Answer."}]}),
        ]
        for messages in requests:
            ids = self.svc.encode_prompt(messages, None, {})
            prompt = self.tok.decode(ids)
            self.assertIn(original, prompt)
            self.assertNotIn("中文思考。", prompt)
        # Provenance restoration never changes ordinary user/answer text.
        prompt = self.tok.decode(self.svc.encode_prompt([{"role":"user","content":"中文思考。"}], None, {}))
        self.assertIn("中文思考。", prompt)
        self.path.write_text('{"enabled":false}', encoding="utf-8")
        self.manager.refresh()
        self.assertEqual(self.manager.restore_messages(requests[0])[0]["reasoning_content"], original)

    def test_responses_continuation_carries_english_without_history_database(self):
        asm = responses.Assembler({}, "m", 1, {}, True, False)
        asm.feed(Event("reasoning", "中文", original_text="Original English."))
        asm.feed(Event("reasoning", "思考。", original_text=""))
        asm.close()
        item = asm.response["output"][0]
        self.assertEqual(item["content"][0]["text"], "中文思考。")
        messages = responses.input_messages({"input":[item, {"role":"assistant", "content":"Answer."}]})
        self.assertEqual(messages[0]["reasoning_content"], "Original English.")

    def test_native_exception_reaches_the_api_thread(self):
        def source():
            raise ValueError("native failure")
            yield
        with self.assertRaisesRegex(ValueError, "native failure"):
            list(self.svc.outgoing(source(), threading.Event()))


if __name__ == "__main__":
    unittest.main()
