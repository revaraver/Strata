"""Regression coverage for literal code in translated reasoning (no model load)."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from serve.frontend import Event
from serve.nmt_translation import Marian
from serve.reasoning_translation import DEFAULTS, TranslationManager, needs_translation, take_segment
from serve.translation_text import protect_text


AHK = "```ahk\nif (state)\n    SendInput, {LAlt down\nelse\n    SendInput, {%sn% up}\n```\n"


class CodeProtection(unittest.TestCase):
    def test_inline_code_is_exact_even_when_the_original_is_invalid(self):
        source = "The command `SendInput, {LAlt down` is missing a closing brace."
        plan = protect_text(source)
        self.assertNotIn("SendInput", plan.masked)
        self.assertEqual(plan.restore("命令ABC0缺少右花括号。"),
                         "命令`SendInput, {LAlt down`缺少右花括号。")

    def test_technical_references_and_quoted_commands_survive(self):
        values = ["AHI.GetKeyboardId(0x046D, 0xC539)", "AutoHotkeyU64.exe", "k380.ahk",
                  "Lib\\AutoHotInterception.ahk", "A_TickCount", "Caps+Z", "0x418C",
                  "#Include", "'SendInput, {LAlt down'", '"keyboard"',
                  "'new AutoHotInterception'", "'global altPressTime, altReleaseTime, DBL_GAP, TAP_MAX'",
                  "-NoProfile", "/CP65001"]
        for literal in values:
            context = "The service " if literal == '"keyboard"' else "Check "
            source = context + literal + " before continuing."
            with self.subTest(literal=literal):
                plan = protect_text(source)
                self.assertNotIn(literal, plan.masked)
                self.assertEqual(plan.restore(plan.masked), source)
                self.assertIn(literal, plan.literals)

    def test_decoder_cannot_drop_duplicate_or_invent_code(self):
        plan = protect_text("Compare `one()` with `two()`.")
        for output in ["只有ABC0。", "ABC0 ABC0 ABC1", "ABC0 ABC1 ABC2"]:
            with self.subTest(output=output):
                self.assertIsNone(plan.restore(output))
        self.assertEqual(plan.restore("abc 0 与 ABC1"), "`one()` 与 `two()`")
        self.assertEqual(plan.restore("ABC1 后面是 ABC0"), "`two()` 后面是 `one()`")

    def test_existing_marker_names_do_not_collide_on_restore(self):
        source = "Compare ABC0 with ABC1."
        plan = protect_text(source)
        self.assertEqual(plan.restore(plan.masked), source)

    def test_multiline_inline_code_and_calls_keep_their_nested_literals(self):
        for literal in ["`if (state)\n    SendInput, {LAlt down\n`",
                        'AHI.GetKeyboardId(\n    0x046D, "Lib\\x64\\interception.dll"\n)']:
            with self.subTest(literal=literal):
                source = "Check " + literal + " before continuing."
                plan = protect_text(source)
                self.assertIn(literal, plan.literals)
                self.assertEqual(plan.restore(plan.masked), source)

    def test_only_prose_needs_translation(self):
        for source in [AHK, "    SendInput, {LAlt down\n", "`isDoubleTap(A_TickCount)`", "k380.ahk"]:
            with self.subTest(source=source):
                self.assertFalse(needs_translation(source))
        self.assertTrue(needs_translation("The call `isDoubleTap(A_TickCount)` is valid."))
        self.assertFalse(needs_translation("调用 `isDoubleTap(A_TickCount)` 是有效的。"))
        self.assertTrue(needs_translation("Check `name = '中文'` before continuing."))
        self.assertTrue(needs_translation("Return to the previous window after the command finishes."))
        self.assertTrue(needs_translation("Global settings can be changed later."))

    def test_character_stream_does_not_split_code_or_lose_text(self):
        source = "The command is below.\n" + AHK + "Then check `foo(\"Stop. Wait!\")` again.\n"
        for width in [1, 2, 7, len(source)]:
            with self.subTest(width=width):
                buffer, parts = "", []
                for start in range(0, len(source), width):
                    buffer += source[start:start + width]
                    while True:
                        part, buffer = take_segment(buffer, DEFAULTS, 1)
                        if not part:
                            break
                        parts.append(part)
                while buffer:
                    part, buffer = take_segment(buffer, DEFAULTS, 0, final=True)
                    self.assertTrue(part)
                    parts.append(part)
                self.assertEqual("".join(parts), source)
                self.assertIn(AHK, parts)
                self.assertTrue(any('`foo("Stop. Wait!")`' in part for part in parts))

    def test_unfinished_code_waits_and_final_tail_is_preserved(self):
        for source in ["```ahk\nSendInput, {LAlt down\n", "`AHI.GetKeyboardId(0x046D, 0xC539)",
                       "AHI.GetKeyboardId(0x046D, "]:
            with self.subTest(source=source):
                self.assertEqual(take_segment(source, DEFAULTS, 100), ("", source))
                self.assertEqual(take_segment(source, DEFAULTS, 100, final=True), (source, ""))

    def test_legacy_timed_cutoff_also_keeps_code_whole(self):
        source = "`" + "SendInput, {LAlt down " * 15 + "` tail"
        cfg = {**DEFAULTS, "sentence_mode": False, "segment_chars": 24}
        part, rest = take_segment(source, cfg, 100)
        self.assertEqual(part, source[:source.rfind("`") + 1])
        self.assertEqual(part + rest, source)

    def test_code_only_stream_never_starts_translation_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translation.json"
            path.write_text(json.dumps({"enabled": True, "auto_start": False}), encoding="utf-8")
            manager = TranslationManager(path)
            try:
                source = iter([*(('event', Event('reasoning', c)) for c in AHK), ('done', {})])
                with mock.patch.object(manager, "_ready", side_effect=AssertionError("code reached NMT")):
                    rows = list(manager.wrap(source, threading.Event()))
                self.assertEqual("".join(event.text or "" for kind, event in rows if isinstance(event, Event)), AHK)
                self.assertEqual("".join(event.original_text or "" for kind, event in rows if isinstance(event, Event)), AHK)
                self.assertEqual(rows[-1][0], "done")
            finally:
                manager.close()

    def test_backend_masks_input_and_falls_back_if_decoder_damages_marker(self):
        source = "The command `SendInput, {LAlt down` is missing a brace."
        for decoded, expected in [("命令ABC0缺少花括号。", "命令`SendInput, {LAlt down`缺少花括号。"),
                                  ("命令代码0缺少花括号。", source)]:
            with self.subTest(decoded=decoded):
                model = Marian.__new__(Marian)
                model.backend, model.beam_size = "t5", 2
                model.source = mock.Mock()
                model.source.encode.return_value = [1, 2]
                model.source.unk_id.return_value = 0
                model.source.id_to_piece.side_effect = lambda i: str(i)
                model.target = mock.Mock()
                model.target.decode.return_value = decoded
                model.translator = mock.Mock()
                model.translator.translate_batch.return_value = [SimpleNamespace(hypotheses=[["text", "</s>"]])]
                rows = []
                model.stream_batch([source], rows.append, threading.Event())
                self.assertEqual(rows, [("token", 0, expected), ("done", 0, None)])
                self.assertNotIn("SendInput", model.source.encode.call_args.args[0])
                self.assertIn("ABC0", model.source.encode.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
