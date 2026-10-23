from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config

CST = timezone(timedelta(hours=8))
BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize(name: str, fallback: str = "chat") -> str:
    name = BAD.sub("_", name or "").strip().strip(".")
    if not name:
        name = fallback
    if len(name) > 80:
        name = name[:80]
    return name


def escape(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace("`", "\\`")
    text = text.replace("*", "\\*").replace("_", "\\_")
    text = text.replace("[", "\\[").replace("]", "\\]")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"(?m)^(#{1,6})", r"\\\1", text)
    return text


def human_size(n: int | None) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def rel_from_note(path: str | None, note: Path, vault: Path) -> str:
    if not path:
        return ""
    target = vault / path
    try:
        return os.path.relpath(target, note.parent).replace("\\", "/")
    except ValueError:
        return target.as_posix()


def md_dest(path: str) -> str:
    """Wrap destinations containing spaces/parentheses in angle brackets.

    Markdown terminates `](...)` at the first `)`, which breaks filenames like
    `报表(1).xlsx`. `<...>` is the standard escape for such destinations and is
    supported by Obsidian.
    """
    if any(c in path for c in " ()<>"):
        return f"<{path}>"
    return path


def read_manual_tags(note: Path) -> list[str]:
    if not note.exists():
        return []
    try:
        text = note.read_text(encoding="utf-8")
    except Exception:
        return []
    m = re.search(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return []
    fm = m.group(1)
    tags = []
    tm = re.search(r"^tags:[ \t]*(.*)$", fm, re.M)
    if tm:
        inline = tm.group(1).strip()
        if inline.startswith("["):
            tags = [t.strip().strip('"\'') for t in inline.strip("[]").split(",") if t.strip()]
        else:
            for line in fm[tm.end():].splitlines():
                if line.startswith("  - ") or line.startswith("- "):
                    tags.append(line.split("-", 1)[1].strip().strip('"\''))
                elif line.strip() and not line.startswith(" "):
                    break
    return [t for t in tags if t]


def media_row(con: sqlite3.Connection, media_id: str | None):
    if not media_id:
        return None
    r = con.execute(
        "SELECT media_id, kind, format, width, height, size, export_path, "
        "thumb_path, status FROM media WHERE media_id=?", (media_id,)
    ).fetchone()
    if not r:
        return None
    return {
        "media_id": r[0], "kind": r[1], "format": r[2], "width": r[3],
        "height": r[4], "size": r[5], "export_path": r[6],
        "thumb_path": r[7], "status": r[8],
    }


def render_message(m: dict, media: dict | None, note: Path, vault: Path) -> str:
    dt = datetime.fromtimestamp(m["ts"], CST)
    sender = "我" if m["is_self"] else (m["sender_name"] or m["sender_id"] or "?")
    head = f"- `{dt.strftime('%H:%M')}` **{escape(sender)}**："
    content = m["content"] or ""
    kind = m["media_kind"]

    if kind == "image" and media and media.get("export_path"):
        full = rel_from_note(media["export_path"], note, vault)
        thumb = rel_from_note(media.get("thumb_path"), note, vault)
        if thumb:
            body = f"[![图片]({thumb})]({full})"
        else:
            body = f"![图片]({full})"
    elif kind == "video":
        if media and media.get("export_path"):
            link = rel_from_note(media["export_path"], note, vault)
            body = f"[▶ 视频（{human_size(media.get('size'))}）]({link})"
        else:
            body = "[视频缺失]"
    elif kind == "file":
        if media and media.get("export_path"):
            link = md_dest(rel_from_note(media["export_path"], note, vault))
            body = f"[📎 {escape(m['media_title'] or '文件')}（{human_size(media.get('size'))}）]({link})"
        else:
            body = f"[文件缺失] {escape(m['media_title'] or '')}".strip()
    else:
        body = escape(content).replace("\n", "<br>")

    return f"{head}{body}\n  <!-- wb:msg:{m['msg_id']} -->\n"


def render_chat_month(cfg: Config, con: sqlite3.Connection, chat: dict,
                      month: str, messages: list[dict]) -> Path:
    chat_type = chat["chat_type"]
    sub = "群聊" if chat_type == "group" else ("公众号" if chat_type == "official" else "私聊")
    folder = sanitize(f"{chat['display_name']} ({chat['chat_id']})", chat["chat_id"])
    note = cfg.vault / "10_Chats" / sub / folder / f"{month}.md"
    note.parent.mkdir(parents=True, exist_ok=True)

    first = datetime.fromtimestamp(messages[0]["ts"], CST).isoformat()
    last = datetime.fromtimestamp(messages[-1]["ts"], CST).isoformat()
    participants = []
    for m in messages:
        n = m["sender_name"]
        if n and n not in participants:
            participants.append(n)
        if len(participants) >= 50:
            break

    auto_tags = ["wechat", f"wechat/{sub}"]
    manual = read_manual_tags(note)
    tags = []
    for t in auto_tags + manual:
        if t not in tags:
            tags.append(t)

    lines = [
        "---",
        "type: wechat-chat",
        f"account: {chat.get('account') or ''}",
        f"chat_id: {json.dumps(chat['chat_id'], ensure_ascii=False)}",
        f"chat_name: {json.dumps(chat['display_name'], ensure_ascii=False)}",
        f"chat_type: {chat_type}",
        f'month: "{month}"',
        f"message_count: {len(messages)}",
        f"first_ts: {first}",
        f"last_ts: {last}",
        "participants: [" + ", ".join(json.dumps(p, ensure_ascii=False) for p in participants) + "]",
        "tags:",
    ]
    lines += [f"  - {t}" for t in tags]
    lines += ["---", "", f"# {chat['display_name']} · {month}", "",
              f"> 会话 ID：`{chat['chat_id']}` ｜ 本月 {len(messages)} 条", ""]

    current_day = None
    for m in messages:
        day = datetime.fromtimestamp(m["ts"], CST).strftime("%Y-%m-%d")
        if day != current_day:
            current_day = day
            lines.append(f"## {day}")
            lines.append("")
        media = media_row(con, m.get("media_id"))
        lines.append(render_message(m, media, note, cfg.vault))

    text = "\n".join(lines).rstrip() + "\n"
    if note.exists():
        try:
            if note.read_text(encoding="utf-8") == text:
                return note
        except OSError:
            pass
    tmp = note.with_name(f".{note.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, note)
    return note


def render_all(cfg: Config, con: sqlite3.Connection) -> dict:
    con.row_factory = sqlite3.Row
    chats = [dict(r) for r in con.execute("SELECT * FROM chats ORDER BY last_ts DESC")]
    total_notes = 0
    for chat in chats:
        months = [r[0] for r in con.execute(
            "SELECT DISTINCT strftime('%Y-%m', ts, 'unixepoch', '+8 hours') AS m "
            "FROM messages WHERE chat_id=? ORDER BY m", (chat["chat_id"],)
        )]
        for month in months:
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM messages WHERE chat_id=? "
                "AND strftime('%Y-%m', ts, 'unixepoch', '+8 hours')=? "
                "ORDER BY ts, local_id", (chat["chat_id"], month)
            )]
            if not rows:
                continue
            render_chat_month(cfg, con, chat, month, rows)
            total_notes += 1
    return {"chats": len(chats), "notes": total_notes}


def render_index(cfg: Config, con: sqlite3.Connection) -> None:
    idx = cfg.vault / "30_Index"
    idx.mkdir(parents=True, exist_ok=True)
    (idx / "会话总览.md").write_text(
        "---\ntype: wechat-index\ntags: [wechat]\n---\n\n"
        "# 会话总览\n\n"
        "```dataview\n"
        "TABLE chat_type AS 类型, message_count AS 消息数, last_ts AS 最近消息\n"
        "FROM \"10_Chats\"\n"
        "WHERE type = \"wechat-chat\"\n"
        "SORT last_ts DESC\n"
        "```\n",
        encoding="utf-8",
    )
    (idx / "客户跟进.md").write_text(
        "---\ntype: wechat-index\ntags: [wechat]\n---\n\n"
        "# 客户跟进\n\n"
        "```dataview\n"
        "TABLE chat_name AS 客户, message_count AS 消息数, last_ts AS 最近\n"
        "FROM \"10_Chats\"\n"
        "WHERE contains(tags, \"客户\")\n"
        "SORT last_ts DESC\n"
        "```\n",
        encoding="utf-8",
    )
    (idx / "使用说明.md").write_text(
        "---\ntype: wechat-index\ntags: [wechat]\n---\n\n"
        "# 使用说明\n\n"
        "- 全局搜索：`Ctrl+Shift+F`，输入客户名、项目关键词。\n"
        "- 按会话筛选：打开 [[会话总览]]，用 Dataview 表格。\n"
        "- 人工标签：在月份笔记的 `tags` 里加 `客户`、`项目` 等，重渲染会保留。\n"
        "- 图片：正文显示缩略图，点击打开原图。\n"
        "- 视频/文件：点击链接直接打开本地副本。\n",
        encoding="utf-8",
    )
