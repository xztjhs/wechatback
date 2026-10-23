from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import archive
from .config import Config

BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize(name: str, fallback: str = "file") -> str:
    name = BAD.sub("_", name or "").strip().strip(".")
    if not name:
        name = fallback
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:110] + "." + ext) if dot and len(ext) <= 10 else name[:120]
    return name


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def process_one(item, cfg: Config) -> dict:
    source_key, kind, source_path, title, size = item
    if not source_path:
        return {"source_key": source_key, "status": "missing", "error": "source_path is null"}
    src = Path(source_path)
    if not src.exists():
        return {"source_key": source_key, "status": "missing", "error": str(src)}
    try:
        sha = sha256_file(src)
        if kind == "video":
            dst = cfg.vault / "20_Media" / "Videos" / f"{sha}{src.suffix or '.mp4'}"
        else:
            name = sanitize(title or src.name, src.name)
            dst = cfg.vault / "20_Media" / "Files" / f"{sha}_{name}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Always copy for correctness on SMB (stale exists() can skip a real write).
        tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        return {
            "source_key": source_key, "status": "ok", "error": None,
            "media": {
                "media_id": sha, "kind": kind,
                "format": (src.suffix or "").lstrip(".").lower(),
                "width": None, "height": None, "size": src.stat().st_size,
                "export_path": str(dst.relative_to(cfg.vault)).replace("\\", "/"),
                "thumb_path": None,
                "source_path": str(src), "source_key": source_key,
                "status": "ok",
                "first_seen": int(time.time()), "last_seen": int(time.time()),
            },
        }
    except Exception as e:
        return {"source_key": source_key, "status": "failed", "error": str(e)[:300]}


def process_files(cfg: Config, con: sqlite3.Connection) -> dict:
    rows = con.execute(
        "SELECT source_key, kind, source_path, title, size FROM pending_media "
        "WHERE kind IN ('video','file') AND status='pending'"
    ).fetchall()
    if not rows:
        return {"total": 0, "ok": 0, "failed": 0, "missing": 0}
    stats = {"total": len(rows), "ok": 0, "failed": 0, "missing": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=cfg.copy_workers) as ex:
        futs = [ex.submit(process_one, r, cfg) for r in rows]
        for fut in as_completed(futs):
            r = fut.result()
            done += 1
            if r["status"] == "ok":
                archive.upsert_media(con, r["media"])
                archive.attach_media(con, r["media"]["media_id"], r["source_key"])
                archive.set_pending_status(con, r["source_key"], "done", media_id=r["media"]["media_id"])
                stats["ok"] += 1
            elif r["status"] == "missing":
                archive.set_pending_status(con, r["source_key"], "missing", error=r.get("error"))
                stats["missing"] += 1
            else:
                archive.set_pending_status(con, r["source_key"], "failed", error=r.get("error"))
                stats["failed"] += 1
            if done % 50 == 0:
                con.commit()
                print(f"  media {done}/{len(rows)} ok={stats['ok']} fail={stats['failed']} missing={stats['missing']}", flush=True)
    con.commit()
    return stats
