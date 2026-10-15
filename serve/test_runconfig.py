"""serve/test_runconfig.py - #564: the web page's Settings view of the run config (GET / POST /config), against the
mock engine: only the listed keys change, every other key of the file stays, a bad value changes nothing, and only
Strata's own page may write it (JSON, no foreign Origin, the API key when one is set).

    python -m unittest serve.test_runconfig
"""
from __future__ import annotations

import contextlib
import concurrent.futures
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
import threading
import time
from pathlib import Path
from unittest import mock

from serve import runconfig
from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, MockEngine, Service, serve

ROOT = Path(__file__).resolve().parents[1]
CFG = {"exe": "engine/strata.exe", "args": ["--pack", "data/packs/q2_0", "--kv", "int8", "--spec-min-p", "0.5"],
       "model_name": "qwen3.8-flash-next-q2_0", "port": 8080, "host": "0.0.0.0", "api_key": "",
       "sampling": {"temperature": 0.7, "presence_penalty": 1.5},
       "mcp_servers": {"files": {"command": "npx", "args": ["-y", "server-filesystem", "."]}},
       "cors_origins": ["http://localhost:3000"]}


class Apply(unittest.TestCase):
    def test_only_the_named_keys_change(self):
        new, changed = runconfig.apply(CFG, {"sampling.top_p": 0.95, "reasoning_budget_tokens": 4096,
                                             "vram_reserve_mib": 2048, "aliases": "qwen, local", "lazy_load": True})
        self.assertEqual(changed, ["sampling.top_p", "reasoning_budget_tokens", "vram_reserve_mib", "aliases",
                                   "lazy_load"])
        self.assertEqual(new["sampling"], {"temperature": 0.7, "presence_penalty": 1.5, "top_p": 0.95})
        self.assertEqual(new["args"][-2:], ["--vram-reserve-mib", "2048"])
        self.assertEqual(new["aliases"], ["qwen", "local"])
        for k in ("exe", "model_name", "port", "host", "api_key", "mcp_servers", "cors_origins"):
            self.assertEqual(new[k], CFG[k], k)
        self.assertEqual(CFG["sampling"], {"temperature": 0.7, "presence_penalty": 1.5})   # the input is left alone

    def test_null_is_the_default_again(self):
        cfg, _ = runconfig.apply(CFG, {"vram_reserve_mib": 2048, "fit_max_tokens": True})
        new, changed = runconfig.apply(cfg, {"vram_reserve_mib": None, "fit_max_tokens": None,
                                             "sampling.temperature": None, "idle_unload_s": None})
        self.assertEqual(changed, ["vram_reserve_mib", "fit_max_tokens", "sampling.temperature"])
        self.assertEqual(new["args"], CFG["args"])
        self.assertNotIn("fit_max_tokens", new)
        self.assertEqual(new["sampling"], {"presence_penalty": 1.5})
        new, _ = runconfig.apply({"sampling": {"top_k": 20}}, {"sampling.top_k": None})
        self.assertNotIn("sampling", new)                                 # an empty block goes

    def test_refused(self):
        for changes in ({"api_key": "x"}, {"host": "0.0.0.0"}, {"mcp_servers": {}}, {"before_load": "calc.exe"},
                        {"args": []}, {"exe": "evil.exe"}, {"trusted_origins": ["*"]}, {"allowed_hosts": ["*"]},
                        {"sampling.temperature": -1}, {"sampling.top_p": 0}, {"sampling.top_k": 65},
                        {"sampling.min_p": 2}, {"reasoning_budget_tokens": 1.5}, {"fit_max_tokens": "yes"},
                        {"effort_position": "middle"}, {"vram_reserve_mib": -5}, {"idle_unload_s": True},
                        {"aliases": [1]}, {}, None, ["x"]):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                runconfig.apply(CFG, changes)
        with self.assertRaises(ValueError):                              # one bad value: nothing is applied
            runconfig.apply(CFG, {"fit_max_tokens": True, "sampling.top_k": 0})
        with self.assertRaises(ValueError):
            runconfig.apply({**CFG, "vision": {"gpu": True}}, {"lazy_load": True})

    def test_view(self):
        v = runconfig.view({**CFG, "args": CFG["args"] + ["--vram-reserve-mib", "1500"]}, "x/strata-q2_0.json")
        self.assertEqual(v["file"], "strata-q2_0.json")
        got = {k["key"]: k for k in v["keys"]}
        self.assertEqual(got["sampling.temperature"]["value"], 0.7)
        self.assertEqual(got["vram_reserve_mib"]["value"], 1500)
        self.assertIsNone(got["fit_max_tokens"]["value"])
        self.assertEqual(got["anthropic_thinking"]["choices"], ["model", "on_request"])
        self.assertNotIn("api_key", got)
        self.assertNotIn("mcp_servers", got)

    def test_parallel_and_ram_cache_round_trip(self):
        changes = {"parallel": 4, "batch_mtp": True, "conversation_cache_mib": 32768,
                   "conversation_cache_slots": 4, "conversation_cache_min_free_mib": 8192}
        new, changed = runconfig.apply(CFG, changes)
        self.assertEqual(set(changed), set(changes))
        self.assertEqual(new["parallel"], 4)
        self.assertIn("--batch-mtp", new["args"])
        for key, value in changes.items():
            self.assertEqual(runconfig.value_of(new, key), value)
        for key in ("host", "api_key", "exe", "mcp_servers", "sampling"):
            self.assertEqual(new[key], CFG[key])
        same, changed = runconfig.apply(new, changes)
        self.assertEqual(changed, [])
        self.assertEqual(same, new)

    def test_disabling_parallel_clears_legacy_override(self):
        cfg = {**CFG, "args": CFG["args"] + ["--batch", "4", "--batch-mtp"]}
        self.assertEqual(runconfig.value_of(cfg, "parallel"), 4)
        new, _ = runconfig.apply(cfg, {"parallel": 1, "batch_mtp": False})
        self.assertEqual(new["parallel"], 1)
        self.assertNotIn("--batch", new["args"])
        self.assertNotIn("--batch-mtp", new["args"])
        self.assertEqual(new["args"], CFG["args"])

    def test_duplicate_cache_options_are_canonicalized(self):
        cfg = {**CFG, "args": CFG["args"] + ["--conversation-cache-mib", "8192",
                                             "--conversation-cache-mib", "16384"]}
        self.assertEqual(runconfig.value_of(cfg, "conversation_cache_mib"), 16384)
        new, _ = runconfig.apply(cfg, {"conversation_cache_mib": 32768})
        self.assertEqual(new["args"].count("--conversation-cache-mib"), 1)
        cleared, _ = runconfig.apply(new, {"conversation_cache_mib": None})
        self.assertEqual(cleared["args"], CFG["args"])

    def test_parallel_cache_validation_is_atomic(self):
        for changes in ({"parallel": 0}, {"parallel": 2.5}, {"parallel": True},
                        {"batch_mtp": "true"}, {"conversation_cache_slots": 0},
                        {"conversation_cache_mib": -1}, {"conversation_cache_min_free_mib": 1.5},
                        {"parallel": float("inf")}, {"conversation_cache_mib": float("nan")},
                        {"parallel": 4, "conversation_cache_mib": -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                runconfig.apply(CFG, changes)
        self.assertEqual(CFG["args"], ["--pack", "data/packs/q2_0", "--kv", "int8", "--spec-min-p", "0.5"])

    def test_chinese_schema_retains_machine_keys(self):
        entries = {k["key"]: k for k in runconfig.view(CFG, "strata-iq3_s.json")["keys"]}
        self.assertEqual(entries["parallel"]["label"], "并行请求数")
        self.assertEqual(entries["parallel"]["min"], 1)
        self.assertEqual(entries["parallel"]["step"], 1)
        self.assertEqual(entries["batch_mtp"]["kind"], "bool")
        self.assertIn("退出服务后清空", entries["conversation_cache_mib"]["help"])
        self.assertNotIn("api_key", entries)


class Http(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "strata-q2_0.json"
        self.path.write_text(json.dumps(CFG, indent=1), encoding="utf-8")
        tok = ByteTokenizer()
        self.svc = Service(MockEngine(tok, "ok", max_context=4096), tok, ChatTemplate(ROOT / "serve/chat_template.jinja"),
                           sampling_defaults=CFG["sampling"])
        self.svc.config_path = str(self.path)
        self.httpd = serve(self.svc, port=0)
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.dir.cleanup()

    def call(self, method, body=None, headers=None, path="/config"):
        h = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(f"http://{self.host}{path}", method=method, headers=h,
                                     data=None if body is None else (body if isinstance(body, bytes)
                                                                     else json.dumps(body).encode()))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def saved(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_read_and_write_from_the_page(self):
        status, v = self.call("GET")
        self.assertEqual(status, 200, v)
        self.assertEqual({k["key"]: k["value"] for k in v["keys"]}["sampling.temperature"], 0.7)
        with contextlib.redirect_stdout(io.StringIO()):
            status, v = self.call("POST", {"set": {"sampling.temperature": 1.0, "open_browser": False}},
                                  {"Origin": f"http://{self.host}"})
        self.assertEqual(status, 200, v)
        self.assertEqual(v["changed"], ["sampling.temperature", "open_browser"])
        cfg = self.saved()
        self.assertEqual(cfg["sampling"]["temperature"], 1.0)
        self.assertIs(cfg["open_browser"], False)
        for k in ("mcp_servers", "cors_origins", "host", "args", "exe"):
            self.assertEqual(cfg[k], CFG[k], k)
        self.assertEqual(json.loads((self.path.parent / "strata-q2_0.json.bak").read_text(encoding="utf-8")), CFG)
        status, v = self.call("POST", {"set": {"open_browser": False}})   # no Origin (a script on this PC): allowed
        self.assertEqual((status, v["changed"]), (200, []))

    def test_parallel_cache_http_save_and_backup(self):
        changes = {"parallel": 4, "batch_mtp": True, "conversation_cache_mib": 32768,
                   "conversation_cache_slots": 4, "conversation_cache_min_free_mib": 8192}
        with contextlib.redirect_stdout(io.StringIO()):
            status, result = self.call("POST", {"set": changes})
        self.assertEqual(status, 200, result)
        entries = {k["key"]: k["value"] for k in result["keys"]}
        for key, value in changes.items():
            self.assertEqual(entries[key], value)
        saved = self.saved()
        self.assertEqual(saved["host"], CFG["host"])
        self.assertEqual(saved["api_key"], CFG["api_key"])
        backup = json.loads(self.path.with_name(self.path.name + ".bak").read_text())
        self.assertEqual(backup, CFG)
        before = self.path.read_bytes()
        status, _ = self.call("POST", {"set": {"parallel": 2, "conversation_cache_slots": 0}})
        self.assertEqual(status, 400)
        self.assertEqual(self.path.read_bytes(), before)

    def test_hot_settings_update_live_service_and_reset(self):
        changes = {"sampling.temperature": 0.9, "sampling.top_p": 0.8, "sampling.top_k": 12,
                   "sampling.min_p": 0.1, "reasoning_budget_tokens": 1024, "fit_max_tokens": True,
                   "anthropic_thinking": "on_request", "aliases": "local", "idle_unload_s": 60,
                   "engine_silence_s": 25, "api_monitor": True}
        engine = self.svc.engine
        with self.svc.fifo, contextlib.redirect_stdout(io.StringIO()):
            status, result = self.call("POST", {"set": changes})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["hot_applied"], list(changes))
        self.assertEqual(result["restart_required"], [])
        self.assertEqual(result["restart_pending"], [])
        self.assertEqual(self.svc.reasoning_budget({}), 1024)
        self.assertEqual(self.svc.reasoning_budget({"reasoning_budget_tokens": 0}), None)
        self.assertTrue(self.svc.fit_max_tokens)
        self.assertFalse(self.svc.anthropic_think_unasked)
        self.assertEqual(self.svc.aliases, ["local"])
        self.assertEqual(self.svc.idle_unload_s, 60)
        self.assertEqual(engine.silence_s, 25)
        self.assertTrue(self.svc.api_monitor)
        self.assertIs(self.svc.engine, engine)
        self.assertEqual(self.svc.sampling_defaults, {"temperature": 0.9, "presence_penalty": 1.5,
                         "top_p": 0.8, "top_k": 12, "min_p": 0.1})
        status, models = self.call("GET", path="/v1/models")
        self.assertEqual(status, 200, models)
        self.assertIn("local", [model["id"] for model in models["data"]])
        status, _ = self.call("GET", path="/api/requests")
        self.assertEqual(status, 200)
        with contextlib.redirect_stdout(io.StringIO()):
            status, result = self.call("POST", {"set": {key: None for key in changes}})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.svc.reasoning_budget({}), None)
        self.assertEqual(self.svc.sampling_defaults, {"presence_penalty": 1.5})
        self.assertFalse(self.svc.fit_max_tokens)
        self.assertTrue(self.svc.anthropic_think_unasked)
        self.assertEqual(self.svc.aliases, [])
        self.assertEqual(self.svc.idle_unload_s, 0)
        from serve.server import ENGINE_SILENCE_S
        self.assertEqual(engine.silence_s, ENGINE_SILENCE_S)
        self.assertFalse(self.svc.api_monitor)
        self.assertEqual(self.call("GET", path="/api/requests")[0], 404)

    def test_mixed_save_retains_pending_restart_until_reverted(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status, result = self.call("POST", {"set": {"reasoning_budget_tokens": 512, "parallel": 4}})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["hot_applied"], ["reasoning_budget_tokens"])
        self.assertEqual(result["restart_required"], ["parallel"])
        self.assertEqual(result["restart_pending"], ["parallel"])
        self.assertEqual(self.svc.reasoning_budget({}), 512)
        status, result = self.call("GET")
        self.assertEqual(status, 200)
        self.assertEqual(result["restart_pending"], ["parallel"])
        with contextlib.redirect_stdout(io.StringIO()):
            status, result = self.call("POST", {"set": {"sampling.top_k": 8}})
        self.assertEqual(status, 200)
        self.assertEqual(result["restart_required"], [])
        self.assertEqual(result["restart_pending"], ["parallel"])
        with contextlib.redirect_stdout(io.StringIO()):
            status, result = self.call("POST", {"set": {"parallel": None}})
        self.assertEqual(status, 200)
        self.assertEqual(result["restart_pending"], [])

    def test_model_profiles_http_round_trip_and_hot_apply(self):
        profiles = [{"name": "strata-compress", "thinking": "off"},
                    {"name": "strata-fast", "reasoning_budget_tokens": 1024, "reasoning_effort": "low"}]
        with contextlib.redirect_stdout(io.StringIO()):
            status, result = self.call("POST", {"set": {"model_profiles": profiles}})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["hot_applied"], ["model_profiles"])
        self.assertEqual(result["restart_required"], [])
        self.assertEqual(self.saved()["model_profiles"], profiles)
        self.assertEqual(list(self.svc.model_profiles), ["strata-compress", "strata-fast"])
        # the 分身's explicit policy wins over the client; the base model and plain aliases are untouched
        self.assertEqual(self.svc.reasoning_budget({"model": "strata-fast", "reasoning_budget_tokens": 8192}), 1024)
        self.assertEqual(self.svc.reasoning_budget({"model": "other", "reasoning_budget_tokens": 8192}), 8192)
        self.assertEqual(self.svc.with_shared({"model": "strata-compress", "reasoning_effort": "high",
                                               "messages": []}, "openai")["reasoning_effort"], "none")
        self.assertEqual(self.svc.with_shared({"model": "strata-base", "reasoning_effort": "high",
                                               "messages": []}, "openai")["reasoning_effort"], "high")
        status, models = self.call("GET", path="/v1/models")
        self.assertEqual(status, 200)
        by = {m["id"]: m for m in models["data"]}
        self.assertEqual(by["strata-compress"]["alias_of"], self.svc.model)
        self.assertEqual(by["strata-compress"]["profile"], {"thinking": "off"})
        self.assertEqual(by["strata-fast"]["profile"], {"reasoning_budget_tokens": 1024, "reasoning_effort": "low"})
        self.assertEqual(self.call("GET", path="/props?model=strata-compress")[0], 200)
        # a bad value changes neither the file nor the live service
        before = self.path.read_bytes()
        for bad in ([{"name": CFG["model_name"]}], [{"name": "x", "thinking": "maybe"}],
                    [{"name": "x", "reasoning_budget_tokens": 1.5}], [{"name": "x"}, {"name": "x"}]):
            status, _ = self.call("POST", {"set": {"model_profiles": bad}})
            self.assertEqual(status, 400, bad)
        self.assertEqual(self.path.read_bytes(), before)
        # a combined save must not let a 分身 collide with an alias set in the same request
        status, _ = self.call("POST", {"set": {"model_profiles": [{"name": "twin"}], "aliases": "twin"}})
        self.assertEqual(status, 400)
        with contextlib.redirect_stdout(io.StringIO()):
            status, result = self.call("POST", {"set": {"model_profiles": None}})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.svc.model_profiles, {})
        self.assertNotIn("model_profiles", self.saved())

    def test_budget_and_sampling_affect_next_generation(self):
        from serve.test_server import ThinkingEngine
        from serve.server import REASONING_WRAP_UP
        engine = ThinkingEngine(self.svc.tok)
        self.svc.engine = engine
        with contextlib.redirect_stdout(io.StringIO()):
            status, result = self.call("POST", {"set": {"reasoning_budget_tokens": 20,
                                                        "sampling.temperature": 0.8}})
        self.assertEqual(status, 200, result)
        request = {"model": "local", "messages": [{"role": "user", "content": "2+2?"}], "max_tokens": 400}
        with mock.patch.object(engine, "generate", wraps=engine.generate) as generate:
            status, result = self.call("POST", request, path="/v1/chat/completions")
            self.assertEqual(status, 200, result)
            self.assertEqual(result["choices"][0]["message"]["reasoning_content"],
                             ThinkingEngine.THOUGHT[:20] + REASONING_WRAP_UP.split("</think>")[0])
            self.assertEqual(generate.call_args_list[0].args[2]["temperature"], 0.8)
        self.svc.set_shared({"temperature": 0.6})
        with mock.patch.object(engine, "generate", wraps=engine.generate) as generate:
            status, result = self.call("POST", {**request, "reasoning_budget_tokens": 0, "temperature": 0},
                                       path="/v1/chat/completions")
            self.assertEqual(status, 200, result)
            self.assertEqual(result["choices"][0]["message"]["reasoning_content"], ThinkingEngine.THOUGHT)
            self.assertEqual(generate.call_args_list[0].args[2]["temperature"], 0)

    def test_inflight_generation_keeps_its_original_budget(self):
        from serve.test_server import ThinkingEngine
        from serve.server import REASONING_WRAP_UP
        engine = ThinkingEngine(self.svc.tok)
        self.svc.engine = engine
        self.call("POST", {"set": {"reasoning_budget_tokens": 20}})
        started, release = threading.Event(), threading.Event()
        generate = engine.generate
        first = True

        def paused_generate(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                started.set()
                if not release.wait(5):
                    raise RuntimeError("test did not release the paused request")
            yield from generate(*args, **kwargs)

        engine.generate = paused_generate
        request = {"messages": [{"role": "user", "content": "2+2?"}], "max_tokens": 400}
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.call, "POST", request, path="/v1/chat/completions")
            try:
                self.assertTrue(started.wait(3))
                status, result = self.call("POST", {"set": {"reasoning_budget_tokens": 5}})
                self.assertEqual(status, 200, result)
                self.assertEqual(result["hot_applied"], ["reasoning_budget_tokens"])
            finally:
                release.set()
            status, result = future.result(timeout=5)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["choices"][0]["message"]["reasoning_content"],
                         ThinkingEngine.THOUGHT[:20] + REASONING_WRAP_UP.split("</think>")[0])
        self.assertEqual(self.svc.reasoning_budget({}), 5)

    def test_idle_watcher_can_be_enabled_disabled_and_reused(self):
        from serve.test_server import UnloadableEngine
        engine = UnloadableEngine(self.svc.tok, "ok", max_context=4096)
        self.svc.engine = engine
        self.svc.last_request_at = time.time() - 120
        unloaded = threading.Event()
        original_unload = engine.unload

        def record_unload():
            original_unload()
            unloaded.set()

        engine.unload = record_unload
        try:
            status, result = self.call("POST", {"set": {"idle_unload_s": 60}})
            self.assertEqual(status, 200, result)
            watcher = self.svc._idle_unload_thread
            self.assertTrue(watcher.is_alive())
            status, result = self.call("POST", {"set": {"idle_unload_s": 0}})
            self.assertEqual(status, 200, result)
            self.assertFalse(unloaded.wait(1.2))
            self.assertTrue(engine.alive())
            status, result = self.call("POST", {"set": {"idle_unload_s": 0.01}})
            self.assertEqual(status, 200, result)
            self.assertIs(self.svc._idle_unload_thread, watcher)
            self.assertTrue(unloaded.wait(3))
            self.assertFalse(engine.alive())
        finally:
            self.svc.idle_unload_s = 0
            self.svc._idle_unload_wake.set()

    def test_failed_save_does_not_change_runtime(self):
        self.svc.reasoning_budget_tokens = 2048
        before = self.path.read_bytes()
        status, _ = self.call("POST", {"set": {"reasoning_budget_tokens": 1024, "sampling.top_k": 0}})
        self.assertEqual(status, 400)
        with mock.patch.object(runconfig, "save", side_effect=OSError("test disk error")):
            status, _ = self.call("POST", {"set": {"reasoning_budget_tokens": 1024}})
        self.assertEqual(status, 500)
        self.assertEqual(self.svc.reasoning_budget({}), 2048)
        self.assertEqual(self.path.read_bytes(), before)

    def test_only_from_strata_s_own_page(self):
        before = self.path.read_bytes()
        status, _ = self.call("POST", {"set": {"fit_max_tokens": True}}, {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.call("POST", b'{"set": {"fit_max_tokens": true}}', {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        for body in ({"set": {"api_key": ""}}, {"set": {"mcp_servers": {"x": {"command": "calc"}}}},
                     {"set": {"sampling.top_k": 0}}, {"nothing": 1}, b"not json"):
            status, b = self.call("POST", body, {"Origin": f"http://{self.host}"})
            self.assertEqual(status, 400, (body, b))
        self.assertEqual(self.path.read_bytes(), before)                 # nothing was written
        self.assertFalse((self.path.parent / "strata-q2_0.json.bak").exists())

    def test_the_api_key(self):
        self.svc.api_key = "secret"
        self.assertEqual(self.call("GET")[0], 401)
        self.assertEqual(self.call("POST", {"set": {"fit_max_tokens": True}})[0], 401)
        with contextlib.redirect_stdout(io.StringIO()):
            status, _ = self.call("POST", {"set": {"fit_max_tokens": True}}, {"Authorization": "Bearer secret"})
        self.assertEqual(status, 200)
        self.assertIs(self.saved()["fit_max_tokens"], True)

    def test_without_a_run_config(self):
        self.svc.config_path = None
        self.assertEqual(self.call("GET")[0], 404)
        self.assertEqual(self.call("POST", {"set": {"fit_max_tokens": True}})[0], 404)

    def test_the_page_has_the_view(self):
        web = ROOT / "serve" / "web"
        html, js = (web / "index.html").read_text(encoding="utf-8"), (web / "app.js").read_text(encoding="utf-8")
        for el in ("cfg-card", "cfg-form", "cfg-save", "cfg-msg"):
            self.assertIn(f'id="{el}"', html)
        self.assertIn('fetch("config", {method: "POST", headers: headers(true)', js)


if __name__ == "__main__":
    unittest.main()
