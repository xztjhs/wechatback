from __future__ import annotations

import json
import sqlite3
import time

from . import archive, media_file, media_image, normalize, render
from .config import Config

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_state (
  chat_id TEXT PRIMARY KEY,
  last_ts INTEGER,
  updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  mode TEXT,
  started_at INTEGER,
  finished_at INTEGER,
  stats_json TEXT
);
"""


def state_connect(cfg: Config) -> sqlite3.Connection:
    con = sqlite3.connect(cfg.system_dir / "state.db")
    con.executescript(STATE_SCHEMA)
    return con


def load_since(state: sqlite3.Connection) -> dict[str, int]:
    return {
        r[0]: int(r[1])
        for r in state.execute("SELECT chat_id, last_ts FROM chat_state")
        if r[1]
    }


def save_max(state: sqlite3.Connection, max_ts: dict[str, int]) -> None:
    now = int(time.time())
    for chat_id, ts in max_ts.items():
        state.execute(
            "INSERT INTO chat_state(chat_id, last_ts, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "last_ts=MAX(chat_state.last_ts, excluded.last_ts), "
            "updated_at=excluded.updated_at",
            (chat_id, ts, now),
        )
    state.commit()


def run_incremental(cfg: Config, con: sqlite3.Connection, lookback_days: int = 7) -> dict:
    cfg.ensure_dirs()
    state = state_connect(cfg)
    since = load_since(state)
    started = int(time.time())
    try:
        stats = normalize.normalize_all(
            cfg, con, since_by_chat=since, lookback_days=lookback_days
        )
        img = media_image.process_images(cfg, con)
        med = media_file.process_files(cfg, con)
        ren = render.render_all(cfg, con)
        render.render_index(cfg, con)
        save_max(state, stats.get("max_ts_by_chat", {}))
        result = {
            "messages": stats.get("messages", 0),
            "chats": stats.get("chats", 0),
            "pending_media": stats.get("pending_media", 0),
            "images": img,
            "media": med,
            "render": ren,
        }
        state.execute(
            "INSERT INTO runs(mode, started_at, finished_at, stats_json) VALUES(?,?,?,?)",
            ("incremental", started, int(time.time()), json.dumps(result, ensure_ascii=False)),
        )
        state.commit()
        return result
    finally:
        state.close()
