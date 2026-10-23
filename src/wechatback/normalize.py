from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import zstandard

from . import archive
from .config import Config

CST = timezone(timedelta(hours=8))
HEX32 = re.compile(r"^[0-9a-f]{32}$")


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def split_type(v: int) -> tuple[int, int]:
    return v & 0xFFFFFFFF, v >> 32


def strip_sender_prefix(text: str) -> str:
    m = re.match(r"^[A-Za-z0-9_@.\-]+:\n", text)
    return text[m.end():] if m else text


def _text(raw, ct) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if ct == 4:
        try:
            raw = zstandard.ZstdDecompressor().decompress(raw)
        except Exception:
            pass
    return raw.decode("utf-8", "replace")


def _packed_md5(packed) -> str | None:
    if not packed:
        return None
    tail = packed[-32:]
    try:
        s = tail.decode("ascii")
    except Exception:
        return None
    return s if HEX32.match(s) else None


def _xml_tag(text: str, name: str) -> str | None:
    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", text, re.S)
    if not m:
        return None
    v = m.group(1).strip()
    v = re.sub(r"^<!\[CDATA\[|\]\]>$", "", v)
    return v


def _xml_attr(text: str, name: str) -> str | None:
    m = re.search(rf'{name}="([^"]*)"', text)
    return m.group(1) if m else None


def _chat_type(chat_id: str) -> str:
    if chat_id.endswith("@chatroom"):
        return "group"
    if chat_id.startswith("gh_"):
        return "official"
    if chat_id.endswith("@openim"):
        return "openim"
    return "private"


def _month(ts: int) -> str:
    return datetime.fromtimestamp(ts, CST).strftime("%Y-%m")


def _display_name(row: dict, chat_id: str) -> str:
    return (
        (row.get("remark") or "").strip()
        or (row.get("nick_name") or "").strip()
        or (row.get("alias") or "").strip()
        or chat_id
    )


def build_contacts(cfg: Config) -> tuple[dict[str, dict], dict[str, str], dict[str, str]]:
    """Return (contacts by username, md5->username, month_id->month)."""
    contacts: dict[str, dict] = {}
    by_md5: dict[str, str] = {}

    p = cfg.db_dir / "contact" / "contact.db"
    if p.exists():
        con = sqlite3.connect(p)
        con.row_factory = sqlite3.Row
        for r in con.execute(
            "SELECT username, remark, nick_name, alias, verify_flag FROM contact"
        ):
            u = r["username"]
            if not u:
                continue
            contacts[u] = {
                "remark": r["remark"] or "",
                "nick_name": r["nick_name"] or "",
                "alias": r["alias"] or "",
            }
            by_md5[md5(u)] = u
        # group usernames that may not be in contact
        for r in con.execute("SELECT username FROM chat_room"):
            u = r["username"]
            if u and u not in contacts:
                contacts[u] = {"remark": "", "nick_name": "", "alias": ""}
                by_md5[md5(u)] = u
        con.close()

    p = cfg.db_dir / "session" / "session.db"
    if p.exists():
        con = sqlite3.connect(p)
        for (u,) in con.execute("SELECT username FROM SessionTable"):
            if u and u not in contacts:
                contacts[u] = {"remark": "", "nick_name": "", "alias": ""}
                by_md5[md5(u)] = u
        con.close()

    # hardlink dir2id: rowid -> month
    months: dict[int, str] = {}
    p = cfg.db_dir / "hardlink" / "hardlink.db"
    if p.exists():
        con = sqlite3.connect(p)
        for rowid, username in con.execute("SELECT rowid, username FROM dir2id"):
            if re.fullmatch(r"\d{4}-\d{2}", username or ""):
                months[rowid] = username
        con.close()

    return contacts, by_md5, months


def build_video_index(cfg: Config) -> tuple[dict[str, str], dict[tuple[str, int], list[str]]]:
    """Return (md5 -> path, (month, size) -> paths)."""
    by_md5: dict[str, str] = {}
    by_size: dict[tuple[str, int], list[str]] = defaultdict(list)
    p = cfg.db_dir / "hardlink" / "hardlink.db"
    if p.exists():
        con = sqlite3.connect(p)
        months = {}
        for rowid, username in con.execute("SELECT rowid, username FROM dir2id"):
            if re.fullmatch(r"\d{4}-\d{2}", username or ""):
                months[rowid] = username
        for m, fname, size, d1 in con.execute(
            "SELECT md5, file_name, file_size, dir1 FROM video_hardlink_info_v4"
        ):
            month = months.get(d1)
            if month and fname:
                path = cfg.msg_dir / "video" / month / fname
                if path.exists():
                    by_md5[m] = str(path)
                    by_size[(month, int(size))].append(str(path))
        con.close()
    vroot = cfg.msg_dir / "video"
    if vroot.exists():
        for path in vroot.rglob("*.mp4"):
            try:
                by_size[(path.parent.name, path.stat().st_size)].append(str(path))
            except OSError:
                pass
    return by_md5, by_size


def build_hardlink_files(cfg: Config) -> dict[str, str]:
    """md5 -> source path for files, using hardlink.db."""
    out: dict[str, str] = {}
    p = cfg.db_dir / "hardlink" / "hardlink.db"
    if not p.exists():
        return out
    con = sqlite3.connect(p)
    months = {}
    for rowid, username in con.execute("SELECT rowid, username FROM dir2id"):
        if re.fullmatch(r"\d{4}-\d{2}", username or ""):
            months[rowid] = username
    for m, fname, d1 in con.execute(
        "SELECT md5, file_name, dir1 FROM file_hardlink_info_v4"
    ):
        month = months.get(d1)
        if month and fname:
            out[m] = str(cfg.msg_dir / "file" / month / fname)
    con.close()
    return out


def normalize_all(cfg: Config, con: sqlite3.Connection, limit: int = 0,
                  since_by_chat: dict[str, int] | None = None,
                  lookback_days: int = 7) -> dict:
    cfg.ensure_dirs()
    archive.init(con)
    contacts, by_md5, _ = build_contacts(cfg)
    hardlink_files = build_hardlink_files(cfg)
    video_md5, video_size = build_video_index(cfg)

    self_wxid = cfg.wxid
    display_map = {u: _display_name(v, u) for u, v in contacts.items()}

    stats = defaultdict(int)
    chat_meta: dict[str, dict] = {}
    pending: dict[str, tuple] = {}
    max_ts: dict[str, int] = {}

    msg_dbs = sorted(
        list((cfg.db_dir / "message").glob("message_*.db"))
        + list((cfg.db_dir / "message").glob("biz_message_*.db"))
    )
    if not msg_dbs:
        raise RuntimeError(f"未找到解密后的 message_*.db：{cfg.db_dir / 'message'}")

    stop = False
    for db_path in msg_dbs:
        if stop:
            break
        shard = db_path.stem
        con_src = sqlite3.connect(db_path)
        con_src.row_factory = sqlite3.Row
        try:
            name2id = {
                r["rowid"]: r["user_name"]
                for r in con_src.execute("SELECT rowid, user_name FROM Name2Id")
            }
        except sqlite3.Error:
            name2id = {}
        tables = [
            r[0]
            for r in con_src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
            )
        ]
        for table in tables:
            if stop:
                break
            chat_hash = table[4:]
            chat_id = by_md5.get(chat_hash, f"unknown_{chat_hash}")
            if chat_id not in chat_meta:
                info = contacts.get(chat_id, {})
                chat_meta[chat_id] = {
                    "account": self_wxid,
                    "chat_type": _chat_type(chat_id),
                    "display_name": _display_name(info, chat_id),
                    "remark": info.get("remark", ""),
                    "nick_name": info.get("nick_name", ""),
                    "alias": info.get("alias", ""),
                }
            sql = (
                f'SELECT local_id, server_id, local_type, real_sender_id, '
                f'create_time, message_content, packed_info_data, '
                f'WCDB_CT_message_content, status FROM "{table}"'
            )
            params: list = []
            since = (since_by_chat or {}).get(chat_id)
            if since:
                sql += " WHERE create_time >= ?"
                params.append(int(since) - lookback_days * 86400)
            try:
                cur = con_src.execute(sql, params)
            except sqlite3.Error:
                continue
            batch: list[tuple] = []
            for row in cur:
                try:
                    msg = parse_row(
                        row, chat_id, chat_hash, shard, name2id, display_map,
                        self_wxid, cfg, hardlink_files, video_md5, video_size, pending,
                    )
                except Exception:
                    stats["parse_error"] += 1
                    continue
                if msg is None:
                    stats["empty"] += 1
                    continue
                batch.append(msg)
                stats["messages"] += 1
                max_ts[chat_id] = max(max_ts.get(chat_id, 0), msg[5])
                if limit and stats["messages"] >= limit:
                    stop = True
                    break
                if len(batch) >= 2000:
                    archive.insert_messages(con, batch)
                    con.commit()
                    batch.clear()
            if batch:
                archive.insert_messages(con, batch)
                con.commit()
        con_src.close()
        stats["dbs"] += 1

    archive.upsert_pending(con, list(pending.values()))
    con.commit()
    archive.finish_chats(con, chat_meta)
    con.commit()
    stats["chats"] = len(chat_meta)
    stats["pending_media"] = len(pending)
    out = dict(stats)
    out["max_ts_by_chat"] = max_ts
    return out


def parse_row(row, chat_id, chat_hash, shard, name2id, display_map,
              self_wxid, cfg: Config, hardlink_files, video_md5, video_size,
              pending) -> tuple | None:
    local_id = row["local_id"]
    server_id = row["server_id"] or 0
    local_type = int(row["local_type"] or 0)
    base_type, subtype = split_type(local_type)
    ts = int(row["create_time"] or 0)
    sender_id = name2id.get(row["real_sender_id"], "")
    sender_name = display_map.get(sender_id, sender_id) or sender_id
    content = _text(row["message_content"], row["WCDB_CT_message_content"])
    content = strip_sender_prefix(content)
    if not content and base_type not in (3, 34, 43, 47, 49):
        return None

    media = None
    display = content

    if base_type == 1:
        display = content
    elif base_type == 3:
        media = _image_media(content, row["packed_info_data"], chat_hash, ts, cfg, pending)
        display = "[图片]"
    elif base_type == 34:
        vlen = _xml_attr(content, "voicelength") or _xml_attr(content, "length") or ""
        secs = int(vlen) // 1000 if vlen.isdigit() else 0
        display = f"[语音 {secs}s]" if secs else "[语音]"
    elif base_type == 43:
        media = _video_media(content, ts, cfg, video_md5, video_size, pending)
        secs = _xml_attr(content, "playlength") or ""
        display = f"[视频 {secs}s]" if secs else "[视频]"
    elif base_type == 47:
        display = "[表情]"
    elif base_type == 49:
        display, media = _appmsg(content, subtype, ts, cfg, hardlink_files, pending)
    elif base_type == 10000:
        display = content or "[系统消息]"
    else:
        display = content or f"[类型 {base_type}]"

    display = re.sub(r"\s+", " ", display).strip() or f"[类型 {base_type}]"

    if server_id:
        msg_id = f"srv:{chat_id}:{server_id}"
    else:
        msg_id = f"loc:{chat_id}:{shard}:{local_id}"

    media_id = None
    media_kind = media["kind"] if media else None
    media_key = media.get("source_key") if media else None
    media_source = media.get("source_path") if media else None
    media_title = media.get("title") if media else None
    media_size = media.get("size") if media else None

    raw = json.dumps(media, ensure_ascii=False) if media else None
    content_hash = hashlib.sha1(display.encode("utf-8")).hexdigest()
    time_iso = datetime.fromtimestamp(ts, CST).isoformat()

    return (
        msg_id, chat_id, shard, local_id, server_id, ts, time_iso,
        sender_id, sender_name, 1 if sender_id == self_wxid else 0,
        base_type, subtype, display, content_hash,
        media_id, media_kind, media_key, media_source, media_title, media_size,
        "active", raw,
    )


def _image_media(content, packed, chat_hash, ts, cfg: Config, pending) -> dict | None:
    key = _packed_md5(packed)
    if not key:
        return None
    month = _month(ts)
    base = cfg.msg_dir / "attach" / chat_hash / month / "Img"
    for name in (f"{key}.dat", f"{key}_h.dat", f"{key}_t.dat"):
        p = base / name
        if p.exists():
            item = ("img:" + key, "image", str(p), None,
                    int(_xml_attr(content, "length") or 0))
            pending.setdefault(item[0], item)
            return {
                "kind": "image", "key": key, "source_key": "img:" + key,
                "source_path": str(p), "size": item[4],
            }
    return {
        "kind": "image", "key": key, "source_key": "img:" + key,
        "source_path": None, "size": 0,
    }


def _video_media(content, ts, cfg: Config, video_md5, video_size, pending) -> dict | None:
    key = (_xml_attr(content, "md5") or _xml_attr(content, "originsourcemd5") or "").lower()
    if not key:
        return None
    month = _month(ts)
    size = int(_xml_attr(content, "length") or 0)
    src = video_md5.get(key)
    if not src:
        cands = video_size.get((month, size)) or []
        if cands:
            src = cands[0]
    item = ("video:" + key, "video", src, None, size)
    pending.setdefault(item[0], item)
    return {
        "kind": "video", "key": key, "source_key": "video:" + key,
        "source_path": src, "size": size,
    }


def _appmsg(content, subtype, ts, cfg: Config, hardlink_files, pending) -> tuple[str, dict | None]:
    title = _xml_tag(content, "title") or ""
    title = re.sub(r"\s+", " ", title).strip()

    if subtype == 6:
        md5v = (_xml_tag(content, "md5") or "").lower()
        total = _xml_tag(content, "totallen") or "0"
        size = int(total) if total.isdigit() else 0
        src = hardlink_files.get(md5v)
        if not src and title:
            cand = cfg.msg_dir / "file" / _month(ts) / title
            if cand.exists():
                src = str(cand)
        item = ("file:" + md5v, "file", src, title, size)
        if md5v:
            pending.setdefault(item[0], item)
        return (f"[文件] {title}" if title else "[文件]", {
            "kind": "file", "key": md5v, "source_key": "file:" + md5v,
            "source_path": src, "title": title, "size": size,
        } if md5v else None)

    if subtype == 5:
        return (f"[链接] {title}" if title else "[链接]", None)
    if subtype == 57:
        ref = _xml_tag(content, "content") or ""
        ref = re.sub(r"\s+", " ", ref).strip()[:80]
        return (f"[引用] {ref}" if ref else "[引用]", None)
    if subtype in (2000, 2001):
        return ("[红包]" if subtype == 2001 else "[转账]", None)
    if subtype == 62:
        return ("[拍一拍]", None)
    if title:
        return (f"[卡片] {title}", None)
    return ("[卡片]", None)
