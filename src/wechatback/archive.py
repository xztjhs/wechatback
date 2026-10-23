from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
  chat_id TEXT PRIMARY KEY,
  account TEXT,
  chat_type TEXT,
  display_name TEXT,
  remark TEXT,
  nick_name TEXT,
  alias TEXT,
  first_ts INTEGER,
  last_ts INTEGER,
  message_count INTEGER,
  tags TEXT,
  contact_hash TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  msg_id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL,
  shard TEXT,
  local_id INTEGER,
  server_id INTEGER,
  ts INTEGER,
  time_iso TEXT,
  sender_id TEXT,
  sender_name TEXT,
  is_self INTEGER,
  type INTEGER,
  subtype INTEGER,
  content TEXT,
  content_hash TEXT,
  media_id TEXT,
  media_kind TEXT,
  media_key TEXT,
  media_source TEXT,
  media_title TEXT,
  media_size INTEGER,
  source_status TEXT,
  raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_messages_media_key ON messages(media_key);

CREATE TABLE IF NOT EXISTS media (
  media_id TEXT PRIMARY KEY,
  kind TEXT,
  format TEXT,
  width INTEGER,
  height INTEGER,
  size INTEGER,
  export_path TEXT,
  thumb_path TEXT,
  source_path TEXT,
  source_key TEXT,
  status TEXT,
  first_seen INTEGER,
  last_seen INTEGER
);

CREATE TABLE IF NOT EXISTS pending_media (
  source_key TEXT PRIMARY KEY,
  kind TEXT,
  source_path TEXT,
  title TEXT,
  size INTEGER,
  status TEXT,
  error TEXT,
  media_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_media(status, kind);
"""


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA temp_store=MEMORY")
    return con


def init(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


def reset(con: sqlite3.Connection) -> None:
    for t in ("messages", "media", "pending_media", "chats"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.commit()
    init(con)


def insert_messages(con: sqlite3.Connection, rows: list[tuple]) -> None:
    if not rows:
        return
    con.executemany(
        """
        INSERT OR REPLACE INTO messages (
          msg_id, chat_id, shard, local_id, server_id, ts, time_iso,
          sender_id, sender_name, is_self, type, subtype, content, content_hash,
          media_id, media_kind, media_key, media_source, media_title, media_size,
          source_status, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )


def upsert_pending(con: sqlite3.Connection, items: list[tuple]) -> None:
    if not items:
        return
    con.executemany(
        """
        INSERT INTO pending_media (source_key, kind, source_path, title, size, status)
        VALUES (?,?,?,?,?,'pending')
        ON CONFLICT(source_key) DO UPDATE SET
          source_path=excluded.source_path,
          title=excluded.title,
          size=excluded.size
        """,
        items,
    )


def upsert_media(con: sqlite3.Connection, row: dict) -> None:
    con.execute(
        """
        INSERT INTO media (media_id, kind, format, width, height, size,
                           export_path, thumb_path, source_path, source_key,
                           status, first_seen, last_seen)
        VALUES (:media_id,:kind,:format,:width,:height,:size,
                :export_path,:thumb_path,:source_path,:source_key,
                :status,:first_seen,:last_seen)
        ON CONFLICT(media_id) DO UPDATE SET
          export_path=excluded.export_path,
          thumb_path=excluded.thumb_path,
          source_path=excluded.source_path,
          status=excluded.status,
          last_seen=excluded.last_seen
        """,
        row,
    )


def attach_media(con: sqlite3.Connection, media_id: str, source_key: str) -> None:
    con.execute(
        "UPDATE messages SET media_id=? WHERE media_key=? AND (media_id IS NULL OR media_id='')",
        (media_id, source_key),
    )


def set_pending_status(con: sqlite3.Connection, source_key: str, status: str,
                       error: str | None = None, media_id: str | None = None) -> None:
    con.execute(
        "UPDATE pending_media SET status=?, error=?, media_id=? WHERE source_key=?",
        (status, error, media_id, source_key),
    )


def finish_chats(con: sqlite3.Connection, meta: dict[str, dict]) -> None:
    rows = []
    for chat_id, m in meta.items():
        agg = con.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM messages WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        rows.append(
            (
                chat_id,
                m.get("account", ""),
                m.get("chat_type", "private"),
                m.get("display_name", chat_id),
                m.get("remark", ""),
                m.get("nick_name", ""),
                m.get("alias", ""),
                agg[1],
                agg[2],
                agg[0],
                json.dumps(m.get("tags", []), ensure_ascii=False),
                m.get("contact_hash", ""),
            )
        )
    con.executemany(
        """
        INSERT OR REPLACE INTO chats
        (chat_id, account, chat_type, display_name, remark, nick_name, alias,
         first_ts, last_ts, message_count, tags, contact_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
