"""Local provenance for display-only reasoning translations.

Only exact, previously emitted translations are restored. User text, assistant
answers and reasoning from other providers are left as supplied. SQLite keeps
English originals across Strata restarts, with a fixed retention of 1000 entries.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path


class ReasoningHistory:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.conn = None
        self.closed = False

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()

    def _connection(self):
        if self.conn is None:
            self.conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("CREATE TABLE IF NOT EXISTS originals (id INTEGER PRIMARY KEY, digest TEXT UNIQUE NOT NULL, original TEXT NOT NULL)")
        return self.conn

    def remember(self, original: str, display: str):
        if not original.strip() or original.strip() == display.strip():
            return
        with self.lock:
            if self.closed:
                return
            conn = self._connection()
            conn.execute("INSERT OR REPLACE INTO originals (digest, original) VALUES (?, ?)",
                         (self.key(display), original))
            conn.execute("DELETE FROM originals WHERE id NOT IN (SELECT id FROM originals ORDER BY id DESC LIMIT 1000)")
            conn.commit()

    def restore(self, text: str) -> str:
        if not isinstance(text, str) or not self.path.exists():
            return text
        with self.lock:
            if self.closed:
                return text
            result = self._connection().execute("SELECT original FROM originals WHERE digest = ?", (self.key(text),)).fetchone()
            return result[0] if result else text

    def messages(self, messages):
        # Frontend normalization has already folded each API's thinking blocks
        # into reasoning_content. Work on copies so callers retain their input.
        result = []
        for message in messages:
            if message.get("role") == "assistant" and isinstance(message.get("reasoning_content"), str):
                original = self.restore(message["reasoning_content"])
                if original != message["reasoning_content"]:
                    message = {**message, "reasoning_content": original}
            result.append(message)
        return result

    def close(self):
        with self.lock:
            self.closed = True
            if self.conn is not None:
                self.conn.close()
                self.conn = None
