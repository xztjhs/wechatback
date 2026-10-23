from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from Crypto.Cipher import AES
from PIL import Image

from . import archive
from .config import Config
from .keys import load_or_create_image_key

MARKER = bytes.fromhex("e8f45b657f6d049de701465372f40b47")


def _unpad(b: bytes) -> bytes:
    if not b:
        return b
    n = b[-1]
    if 1 <= n <= 16 and b[-n:] == bytes([n]) * n:
        return b[:-n]
    return b


def decrypt_dat(data: bytes, aes_key: bytes, xor_key: int) -> bytes:
    sig, aes_size, xor_size = struct.unpack("<6sLLx", data[:15])
    if sig != b"\x07\x08V2\x08\x07":
        raise ValueError("not V2 dat")
    aes_len = aes_size + 16
    if 15 + aes_len > len(data):
        raise ValueError("truncated dat")
    dec = AES.new(aes_key, AES.MODE_ECB).decrypt(data[15:15 + aes_len])
    dec = _unpad(dec)
    tail = data[-xor_size:] if xor_size else b""
    return dec + bytes(b ^ xor_key for b in tail)


def _extract_hevc(payload: bytes) -> bytes:
    starts = []
    i = 0
    while i < len(payload) - 3:
        if payload[i:i + 4] == b"\x00\x00\x00\x01":
            starts.append(i)
            i += 4
        elif payload[i:i + 3] == b"\x00\x00\x01":
            starts.append(i)
            i += 3
        else:
            i += 1
    if not starts:
        raise ValueError("no HEVC start code")
    return payload[starts[0]:]


def _ffmpeg_png(hevc: bytes, ffmpeg: Path, tmpdir: Path) -> bytes:
    tmpdir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=tmpdir, suffix=".hevc", delete=False) as f:
        f.write(hevc)
        inp = Path(f.name)
    out = inp.with_suffix(".png")
    try:
        r = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "hevc", "-i", str(inp), "-frames:v", "1", str(out)],
            capture_output=True,
        )
        if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[:200])
        return out.read_bytes()
    finally:
        for p in (inp, out):
            try:
                p.unlink()
            except OSError:
                pass


def payload_to_image(payload: bytes, ffmpeg: Path, tmpdir: Path) -> tuple[bytes, str, int, int]:
    # WXGF / HEVC payload
    if payload[:4] == b"wxgf" or payload[:4] in (b"\x00\x00\x00\x01", b"\x00\x00\x01"):
        hevc = _extract_hevc(payload)
        payload = _ffmpeg_png(hevc, ffmpeg, tmpdir)

    im = Image.open(io.BytesIO(payload))
    fmt = (im.format or "PNG").upper()
    ext = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp",
           "BMP": "bmp", "TIFF": "tiff"}.get(fmt, "png")
    width, height = im.size
    if fmt == "JPEG":
        idx = payload.rfind(b"\xff\xd9")
        if idx > 0:
            payload = payload[:idx + 2]
    return payload, ext, width, height


def _thumb_source(src: Path) -> Path:
    name = src.name
    if name.endswith("_h.dat"):
        cand = src.with_name(name[:-6] + "_t.dat")
        if cand.exists():
            return cand
    if name.endswith("_t.dat"):
        return src
    if name.endswith(".dat"):
        cand = src.with_name(name[:-4] + "_t.dat")
        if cand.exists():
            return cand
    return src


def _save_thumb(img_bytes: bytes, dst: Path, max_edge: int = 480) -> None:
    im = Image.open(io.BytesIO(img_bytes))
    fmt = (im.format or "").upper()
    # Fast path: WeChat's _t.dat is already a small JPEG; save without re-encoding.
    if fmt == "JPEG" and max(im.size) <= max_edge:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(img_bytes)
        os.replace(tmp, dst)
        return
    im.load()
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.thumbnail((max_edge, max_edge), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    im.save(tmp, format="JPEG", quality=85, optimize=True)
    os.replace(tmp, dst)


def _rel(p: Path, root: Path) -> str:
    return str(p.relative_to(root)).replace("\\", "/")


def process_one(item, cfg: Config, aes_key: bytes, xor_key: int) -> dict:
    source_key, kind, source_path, title, size = item
    if not source_path:
        return {"source_key": source_key, "status": "missing", "error": "source_path is null"}
    src = Path(source_path)
    if not src.exists():
        return {"source_key": source_key, "status": "missing", "error": str(src)}
    try:
        payload = decrypt_dat(src.read_bytes(), aes_key, xor_key)
        img_bytes, ext, width, height = payload_to_image(
            payload, cfg.ffmpeg, cfg.work_dir / "tmp"
        )
        sha = hashlib.sha256(img_bytes).hexdigest()
        m = re.search(r"(\d{4})-(\d{2})", source_path)
        year, month = (m.group(1), m.group(2)) if m else (
            time.strftime("%Y"), time.strftime("%m")
        )
        out = cfg.vault / "20_Media" / "Images" / year / month / sha[:2] / f"{sha}.{ext}"
        out.parent.mkdir(parents=True, exist_ok=True)
        # Always write: Path.exists() can return stale True on SMB after a clean,
        # which previously left media rows pointing at missing files.
        tmp = out.with_name(f".{out.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(img_bytes)
        os.replace(tmp, out)

        thumb = cfg.vault / "20_Media" / "Thumbs" / year / month / sha[:2] / f"{sha}.jpg"
        try:
            tsrc = _thumb_source(src)
            if tsrc != src and tsrc.exists():
                tb = decrypt_dat(tsrc.read_bytes(), aes_key, xor_key)
                tb, _, _, _ = payload_to_image(tb, cfg.ffmpeg, cfg.work_dir / "tmp")
            else:
                tb = img_bytes
            _save_thumb(tb, thumb)
        except Exception:
            try:
                _save_thumb(img_bytes, thumb)
            except Exception:
                pass

        return {
            "source_key": source_key, "status": "ok", "error": None,
            "media": {
                "media_id": sha, "kind": "image", "format": ext,
                "width": width, "height": height, "size": len(img_bytes),
                "export_path": _rel(out, cfg.vault),
                "thumb_path": _rel(thumb, cfg.vault) if thumb.exists() else None,
                "source_path": str(src), "source_key": source_key,
                "status": "ok", "first_seen": int(time.time()), "last_seen": int(time.time()),
            },
        }
    except Exception as e:
        return {"source_key": source_key, "status": "failed", "error": str(e)[:300]}


def process_images(cfg: Config, con: sqlite3.Connection) -> dict:
    key = load_or_create_image_key(cfg)
    aes_key = bytes.fromhex(key["aes_hex"])
    xor_key = int(key["xor_key"])
    rows = con.execute(
        "SELECT source_key, kind, source_path, title, size FROM pending_media "
        "WHERE kind='image' AND status='pending'"
    ).fetchall()
    if not rows:
        return {"total": 0, "ok": 0, "failed": 0, "missing": 0}

    stats = {"total": len(rows), "ok": 0, "failed": 0, "missing": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=cfg.image_workers) as ex:
        futs = [ex.submit(process_one, r, cfg, aes_key, xor_key) for r in rows]
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
            if done % 100 == 0:
                con.commit()
                print(f"  images {done}/{len(rows)} ok={stats['ok']} fail={stats['failed']} missing={stats['missing']}", flush=True)
    con.commit()
    return stats
