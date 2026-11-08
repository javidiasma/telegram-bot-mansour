"""Tiny SQLite store: job settings, last posted message ids, and the song cache."""
import json
import sqlite3
import threading
from pathlib import Path

JOB_NAMES = ("dollar", "flights", "songs")


class Storage:
    def __init__(self, path: Path, default_times: dict[str, str]):
        self._lock = threading.Lock()
        self._defaults = {
            name: {
                "time": default_times[name],
                "enabled": True,
                # songs keep old posts by default; dollar & flights replace the previous post
                "delete_prev": name != "songs",
                "last_msg_id": None,
                "last_sent_at": None,
            }
            for name in JOB_NAMES
        }
        self.conn = sqlite3.connect(path, check_same_thread=False)
        with self._lock, self.conn:
            self.conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS songs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL,
                    file_unique_id TEXT UNIQUE,
                    kind TEXT NOT NULL,
                    title TEXT,
                    performer TEXT,
                    sent_count INTEGER NOT NULL DEFAULT 0,
                    added_at TEXT DEFAULT CURRENT_TIMESTAMP)"""
            )

    # ---- key/value -------------------------------------------------------
    def get(self, key: str, default=None):
        with self._lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    # ---- jobs --------------------------------------------------------------
    def job(self, name: str) -> dict:
        return {**self._defaults[name], **self.get(f"job:{name}", {})}

    def update_job(self, name: str, **changes) -> dict:
        data = self.job(name)
        data.update(changes)
        self.set(f"job:{name}", data)
        return data

    # ---- songs -------------------------------------------------------------
    def add_song(self, file_id: str, file_unique_id: str, kind: str, title: str, performer: str) -> bool:
        """Returns False if this exact file was already cached."""
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO songs(file_id, file_unique_id, kind, title, performer) VALUES(?,?,?,?,?)",
                (file_id, file_unique_id, kind, title, performer),
            )
        return cur.rowcount == 1

    def song_count(self) -> int:
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]

    def next_song(self) -> dict | None:
        """Least-played song first, in upload order -> every song is sent once before any repeats."""
        with self._lock:
            row = self.conn.execute(
                "SELECT id, file_id, kind, title, performer FROM songs ORDER BY sent_count, id LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return dict(zip(("id", "file_id", "kind", "title", "performer"), row))

    def mark_song_sent(self, song_id: int) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE songs SET sent_count = sent_count + 1 WHERE id=?", (song_id,))

    def songs_left_in_round(self) -> int:
        with self._lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM songs WHERE sent_count = (SELECT MIN(sent_count) FROM songs)"
            ).fetchone()[0]

    def list_songs(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, title, performer, sent_count FROM songs ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
        return [dict(zip(("id", "title", "performer", "sent_count"), r)) for r in rows]

    def delete_song(self, song_id: int) -> bool:
        with self._lock, self.conn:
            return self.conn.execute("DELETE FROM songs WHERE id=?", (song_id,)).rowcount == 1

    def clear_songs(self) -> int:
        with self._lock, self.conn:
            return self.conn.execute("DELETE FROM songs").rowcount
