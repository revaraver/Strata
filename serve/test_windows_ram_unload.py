"""RAM-unload ownership and session preservation: lifecycle regressions without a GPU."""
import unittest
from pathlib import Path
from unittest.mock import Mock

from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, Service
from serve.test_lifecycle import ResidentEngine


class RamUnload(unittest.TestCase):
    def setUp(self):
        tok = ByteTokenizer()
        self.engine = ResidentEngine(tok)
        self.engine.loaded = True
        self.engine.memory_keeper = Mock(namespace='Local\\Strata-unit', error=None)
        self.engine.session_file = Mock(return_value={'tokens': 100, 'bytes': 1024, 'ms': 1})
        self.svc = Service(self.engine, tok, ChatTemplate(Path(__file__).parent / 'chat_template.jinja'))
        self.svc.unload_session_memory = True

    def test_both_modes_keep_session_and_full_only_releases_weights_after_exit(self):
        for full in (False, True):
            with self.subTest(full=full):
                self.engine.loaded = True
                events = []
                self.engine.memory_keeper.release.side_effect = lambda **kw: events.append(('release',kw,self.engine.alive()))
                self.engine.memory_keeper.release.reset_mock()
                self.assertEqual(self.svc.unload(full=full), 'unloaded')
                self.assertIsInstance(self.svc.session_memory, str)
                self.assertIn('-s-', self.svc.session_memory)
                if full:
                    self.assertEqual(events, [('release', {'kind':'weights'}, False)])
                else:
                    self.assertEqual(events, [])

    def test_save_failure_keeps_engine_alive(self):
        self.engine.session_file.side_effect = ValueError('cannot retain RAM')
        with self.assertRaises(ValueError):
            self.svc.unload(full=True)
        self.assertTrue(self.engine.alive())
        self.engine.memory_keeper.release.assert_not_called()

    def test_residency_failure_is_reported_before_engine_is_unloaded(self):
        self.engine.memory_keeper.error = 'VirtualLock failed'
        with self.assertRaisesRegex(ValueError, 'locked resident'):
            self.svc.unload()
        self.assertTrue(self.engine.alive())
        self.engine.session_file.assert_not_called()

    def test_full_while_already_unloaded_preserves_session(self):
        self.svc.unload()
        kept = self.svc.session_memory
        self.assertEqual(self.svc.unload(full=True), 'not loaded')
        self.assertEqual(self.svc.session_memory, kept)
        self.engine.memory_keeper.release.assert_called_once_with(kind='weights')

    def test_zero_speed_offload_parks_then_the_next_request_restores(self):
        calls = []
        self.engine.vram = lambda r: calls.append(r) or {"vram_free_mib": 1, "expert_cache_mib": 1}
        self.svc.vram_reserve, self.svc.vram_reserve_asked = 4096, True
        self.assertEqual(self.svc.vram(45056, auto_restore=True)["status"], "ok")
        self.assertEqual(self.svc.vram_auto_restore, 4096)          # parked, waiting for the next request
        self.svc._restore_vram_mode()
        self.assertEqual(calls, [45056, 4096])                      # parked at zero, restored to high speed
        self.assertIsNone(self.svc.vram_auto_restore)               # one-shot
        self.svc._restore_vram_mode()
        self.assertEqual(calls, [45056, 4096])                      # the plain zero switch never restores itself
        self.assertEqual(self.svc.vram(45056)["status"], "ok")
        self.assertIsNone(self.svc.vram_auto_restore)
        self.svc._restore_vram_mode()
        self.assertEqual(calls, [45056, 4096, 45056])             # the plain zero switch never restores itself

    def test_failed_restore_keeps_descriptor_and_success_consumes_it(self):
        self.svc.session_memory = 'Local\\Strata-unit-s-example'
        self.engine.session_file.side_effect = ValueError('invalid session')
        with self.assertRaises(ValueError):
            self.svc._session_from_memory()
        self.assertIsNotNone(self.svc.session_memory)
        self.engine.session_file.side_effect = None
        self.svc._session_from_memory()
        self.assertIsNone(self.svc.session_memory)


if __name__ == '__main__':
    unittest.main()
