"""Translation monitor sidecar: one CORS-open JSON endpoint for the widget.

Reads the reasoning-translation history sqlite (WAL, read-only: the engine never
pays for a query), probes the t5 sidecar's /health and mirrors the serve layer's
reasoning_translation status from GET /metrics (the key stays on this side).

    python tools/translation_monitor.py [--port 8097] [--db PATH] [--serve http://127.0.0.1:8080] [--key KEY]

GET /status.json[?n=100]  -> {ok, t, health, rt, segments:[{id, t, original, display}]}
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ARGS = None
CACHE = {"t": 0.0, "rt": None, "health": None}


def probe_health() -> dict:
    base = f"http://127.0.0.1:{ARGS.nmt_port}"
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(base + "/health", timeout=2) as r:
            return {"alive": r.status == 200, "ms": round((time.monotonic() - t0) * 1000, 1)}
    except Exception as exc:
        return {"alive": False, "ms": round((time.monotonic() - t0) * 1000, 1), "error": str(exc)[:120]}


def poll_rt() -> dict:
    req = urllib.request.Request(ARGS.serve + "/metrics",
                                 headers={"Authorization": f"Bearer {ARGS.key}"})
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            d = json.load(r)
        rt = d.get("reasoning_translation") or {}
        eng = d.get("engine") or {}
        return {"ok": True, "enabled": rt.get("enabled"), "active": rt.get("active"),
                "backend": rt.get("backend"), "busy": bool(d.get("busy"))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def segments(n: int) -> list:
    try:
        conn = sqlite3.connect(f"file:{ARGS.db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute("SELECT id, t, original, display FROM originals ORDER BY id DESC LIMIT ?",
                            (max(1, min(n, 1000)),)).fetchall()
    except sqlite3.Error:
        # a database written before the display column exists: originals only
        try:
            rows = [(i, None, o, None) for i, o in conn.execute(
                "SELECT id, original FROM originals ORDER BY id DESC LIMIT ?",
                (max(1, min(n, 1000)),)).fetchall()]
        except sqlite3.Error:
            rows = []
    finally:
        conn.close()
    return [{"id": i, "t": t, "original": o, "display": dsp} for i, t, o, dsp in rows]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path not in ("/status.json", "/health"):
            self.send_error(404)
            return
        n = 100
        try:
            n = int(parse_qs(u.query).get("n", ["100"])[0])
        except ValueError:
            pass
        now = time.monotonic()
        if now - CACHE["t"] > 2.0:
            CACHE["rt"] = poll_rt()
            CACHE["health"] = probe_health()
            CACHE["t"] = now
        body = json.dumps({"ok": True, "t": time.time(), "health": CACHE["health"],
                           "rt": CACHE["rt"], "segments": segments(n)},
                          ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def handle_error(self, request, client_address):
        # the widget aborts in-flight polls on refresh; that is not an error
        import sys
        exc = sys.exc_info()[1]
        if not isinstance(exc, (ConnectionError, BrokenPipeError, TimeoutError)):
            super().handle_error(request, client_address)


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--db", default=r"D:\git\Strata-latest\reasoning-translation.history.sqlite")
    ap.add_argument("--serve", default="http://127.0.0.1:8080")
    ap.add_argument("--key", default="qwe123")
    ap.add_argument("--nmt-port", type=int, default=8096)
    ARGS = ap.parse_args()
    print(f"[translation monitor] http://127.0.0.1:{ARGS.port}/status.json  db={ARGS.db}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
