"""Exercise HTTP admission and one-shot wake, without a GPU or named-memory allocation."""
import concurrent.futures
import json
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock

from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, Service, serve
from serve.test_lifecycle import ResidentEngine


class DevicePark(unittest.TestCase):
    def setUp(self):
        tok = ByteTokenizer()
        self.engine = ResidentEngine(tok)
        self.engine.loaded = True
        self.engine.parked = False
        self.engine.device_park_enabled = True
        self.commands = []
        def command(op, reserve=None):
            self.commands.append((op, reserve))
            self.engine.parked = op == 'PARK'
            return {'ms': 1, 'host_bytes': 1024 if self.engine.parked else 0}
        self.engine.device_command = Mock(side_effect=command)
        self.engine.vram = Mock(return_value={'vram_free_mib': 1})
        self.svc = Service(self.engine, tok, ChatTemplate(Path(__file__).parent / 'chat_template.jinja'))
        self.svc.vram_reserve = 32768
        self.svc.vram_reserve_asked = True
        self.httpd = serve(self.svc, port=0)
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, path, body=None):
        req = urllib.request.Request(self.url+path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.load(response)

    def test_chat_wakes_the_same_engine_at_its_previous_mode(self):
        self.assertEqual(self.request('/unload', {})['status'], 'unloaded')
        self.assertTrue(self.engine.alive())
        self.assertTrue(self.request('/v1/status')['parked'])
        self.assertFalse(self.request('/v1/status')['loaded'])
        self.assertEqual(self.request('/metrics')['live']['state'], 'unloaded')
        chat = {'messages': [{'role': 'user', 'content': 'Hello'}], 'max_tokens': 32, 'reasoning_effort': 'none'}
        answer = self.request('/v1/chat/completions', chat)
        self.assertEqual(answer['choices'][0]['message']['content'], 'Hello.')
        self.assertEqual(self.commands, [('PARK', None), ('WAKE', 32768)])
        self.assertEqual(self.engine.starts, 0)
        self.assertEqual(self.engine.closes, 0)

    def test_concurrent_loads_only_wake_once(self):
        self.svc.unload()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda _: self.svc.load(), range(2)))
        self.assertEqual(self.commands, [('PARK', None), ('WAKE', 32768)])

    def test_unload_is_idempotent_and_a_manual_mode_click_wakes(self):
        self.svc.unload(); self.svc.unload()
        self.svc.vram(4096)
        self.assertEqual(self.commands, [('PARK', None), ('WAKE', 4096)])
        self.assertEqual(self.svc.vram_reserve, 4096)

    def test_failed_wake_does_not_start_a_new_engine_or_lose_parked_state(self):
        self.svc.unload()
        self.engine.device_command.side_effect = ValueError('no VRAM')
        with self.assertRaisesRegex(ValueError, 'no VRAM'):
            self.svc.load()
        self.assertTrue(self.engine.parked)
        self.assertEqual(self.engine.starts, 0)

    def test_plain_zero_keeps_zero_after_a_request(self):
        self.svc.vram(45056)
        self.svc.load()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.svc.vram_reserve, 45056)


if __name__ == '__main__':
    unittest.main()
