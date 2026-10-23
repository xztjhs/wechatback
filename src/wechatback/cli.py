from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from . import __version__, archive, keys, media_file, media_image, normalize, render
from .config import load_config


def cmd_doctor(args) -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    print("WeChatBack", __version__)
    print("data_root :", cfg.data_root)
    print("account   :", cfg.account_dir)
    print("wxid      :", cfg.wxid)
    print("db_storage:", cfg.db_storage)
    print("vault     :", cfg.vault)
    print("system    :", cfg.system_dir)
    print("ffmpeg    :", cfg.ffmpeg, "OK" if cfg.ffmpeg.exists() else "MISSING")

    dbs = list((cfg.db_dir / "message").glob("message_*.db")) if (cfg.db_dir / "message").exists() else []
    print("decrypted :", len(dbs), "message db(s)")
    try:
        k = keys.load_db_keys(cfg)
        print("db keys   :", len([x for x in k if not x.startswith("_")]))
    except Exception as e:
        print("db keys   :", e)
    try:
        ik = keys.load_or_create_image_key(cfg)
        print("image key :", ik["aes_ascii"], "xor=0x%02x" % int(ik["xor_key"]), "uin=", ik["uin"])
    except Exception as e:
        print("image key :", e)
    return 0


def cmd_key(args) -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    if args.action == "show":
        p = cfg.secrets_dir / "image_key.json"
        print(p.read_text(encoding="utf-8") if p.exists() else "no image key cache")
        return 0
    if args.action == "refresh":
        ik = keys.load_or_create_image_key(cfg)
        print(json.dumps(ik, ensure_ascii=False, indent=2))
        return 0
    return 0


def cmd_image_key(args) -> int:
    return cmd_key(argparse.Namespace(action="refresh"))


def cmd_export(args) -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    t0 = time.time()
    con = archive.connect(cfg.archive_db)
    try:
        if args.incremental and not args.full:
            from . import incremental
            print("[*] incremental ...")
            print("[+] incremental:", incremental.run_incremental(cfg, con))
            return 0
        if args.full:
            print("[*] reset archive")
            archive.reset(con)
        print("[*] normalize messages ...")
        stats = normalize.normalize_all(cfg, con, limit=args.limit)
        print("[+] messages:", stats)
        if not args.skip_images:
            print("[*] images ...")
            print("[+] images:", media_image.process_images(cfg, con))
        if not args.skip_files:
            print("[*] videos/files ...")
            print("[+] media:", media_file.process_files(cfg, con))
        if not args.skip_render:
            print("[*] render markdown ...")
            print("[+] render:", render.render_all(cfg, con))
            render.render_index(cfg, con)
            print("[+] index written")
    finally:
        con.close()
    print(f"[+] done in {time.time() - t0:.1f}s")
    return 0


def cmd_media(args) -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    con = archive.connect(cfg.archive_db)
    try:
        if not args.skip_images:
            print("[*] images ...")
            print("[+] images:", media_image.process_images(cfg, con))
        if not args.skip_files:
            print("[*] videos/files ...")
            print("[+] media:", media_file.process_files(cfg, con))
        if not args.skip_render:
            print("[*] render ...")
            print("[+] render:", render.render_all(cfg, con))
            render.render_index(cfg, con)
    finally:
        con.close()
    return 0


def cmd_retry_missing(args) -> int:
    cfg = load_config()
    con = archive.connect(cfg.archive_db)
    kinds = ("image", "video", "file") if args.kind == "all" else (args.kind,)
    q = ",".join("?" * len(kinds))
    cur = con.execute(
        f"UPDATE pending_media SET status='pending', error=NULL "
        f"WHERE status='missing' AND kind IN ({q})",
        kinds,
    )
    con.commit()
    print(f"reset {cur.rowcount} missing item(s): {', '.join(kinds)}")
    print("now run: wechatback media --skip-images  (for video/file)")
    con.close()
    return 0


def cmd_render(args) -> int:
    cfg = load_config()
    con = archive.connect(cfg.archive_db)
    try:
        print(render.render_all(cfg, con))
        render.render_index(cfg, con)
    finally:
        con.close()
    return 0


def _source_sample(cfg, n=100):
    import hashlib
    files = []
    roots = [cfg.msg_dir / "attach", cfg.msg_dir / "video", cfg.msg_dir / "file"]
    for root in roots:
        if not root or not root.exists():
            continue
        for p in root.rglob("*"):
            if p.is_file():
                files.append(p)
                if len(files) >= n:
                    break
        if len(files) >= n:
            break
    out = []
    for p in files[:n]:
        try:
            h = hashlib.sha1(p.open("rb").read(4096)).hexdigest()
            st = p.stat()
            out.append({"path": str(p), "size": st.st_size,
                        "mtime": int(st.st_mtime), "sha1_head": h})
        except OSError:
            continue
    return out


def cmd_verify(args) -> int:
    cfg = load_config()
    if not cfg.archive_db.exists():
        print("archive.db not found")
        return 1
    con = sqlite3.connect(cfg.archive_db)
    for label, sql in [
        ("messages", "SELECT COUNT(*) FROM messages"),
        ("chats", "SELECT COUNT(*) FROM chats"),
        ("media", "SELECT COUNT(*) FROM media"),
        ("pending", "SELECT status, COUNT(*) FROM pending_media GROUP BY status"),
    ]:
        try:
            print(label, con.execute(sql).fetchall())
        except Exception as e:
            print(label, e)

    dats = list((cfg.vault / "10_Chats").rglob("*.dat")) if (cfg.vault / "10_Chats").exists() else []
    dats += list((cfg.vault / "20_Media").rglob("*.dat")) if (cfg.vault / "20_Media").exists() else []
    print("dat_in_vault", len(dats))

    missing = []
    try:
        for mid, kind, path, thumb in con.execute(
            "SELECT media_id, kind, export_path, thumb_path FROM media"
        ):
            if path and not (cfg.vault / path).exists():
                missing.append((mid, kind, path))
    except Exception as e:
        print("media check", e)
    print("media_missing", len(missing))

    snap = cfg.system_dir / "source_snapshot.json"
    sample = _source_sample(cfg, 100)
    if args.snapshot:
        snap.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
        print("source_snapshot", len(sample), "->", snap)
    elif args.no_write and snap.exists():
        import hashlib
        old = {x["path"]: x for x in json.loads(snap.read_text(encoding="utf-8"))}
        changed = []
        for x in sample:
            o = old.get(x["path"])
            if o and (o["size"] != x["size"] or o["mtime"] != x["mtime"] or o["sha1_head"] != x["sha1_head"]):
                changed.append(x["path"])
        print("source_changed", len(changed))
    con.close()
    return 0


def cmd_serve(args) -> int:
    print("media gateway is not implemented yet; videos/files are copied into the vault")
    return 0


def cmd_search(args) -> int:
    cfg = load_config()
    con = sqlite3.connect(cfg.archive_db)
    q = args.query
    rows = con.execute(
        "SELECT time_iso, sender_name, chat_id, content FROM messages "
        "WHERE content LIKE ? ORDER BY ts DESC LIMIT ?", (f"%{q}%", args.limit)
    ).fetchall()
    for r in rows:
        print(f"{r[0]} | {r[1]} | {r[2]} | {r[3][:120]}")
    con.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wechatback", description="WeChat 4.x -> Obsidian")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command")

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    k = sub.add_parser("key")
    k.add_argument("action", nargs="?", default="show", choices=["show", "refresh"])
    k.set_defaults(func=cmd_key)

    ik = sub.add_parser("image-key")
    ik.add_argument("action", nargs="?", default="probe", choices=["probe", "show"])
    ik.add_argument("--samples", type=int, default=20)
    ik.set_defaults(func=cmd_image_key)

    e = sub.add_parser("export")
    e.add_argument("--full", action="store_true")
    e.add_argument("--incremental", action="store_true")
    e.add_argument("--reconcile", action="store_true")
    e.add_argument("--limit", type=int, default=0)
    e.add_argument("--skip-images", action="store_true")
    e.add_argument("--skip-files", action="store_true")
    e.add_argument("--skip-render", action="store_true")
    e.set_defaults(func=cmd_export)

    m = sub.add_parser("media")
    m.add_argument("--skip-images", action="store_true")
    m.add_argument("--skip-files", action="store_true")
    m.add_argument("--skip-render", action="store_true")
    m.set_defaults(func=cmd_media)

    rm = sub.add_parser("retry-missing")
    rm.add_argument("--kind", choices=["all", "image", "video", "file"], default="all")
    rm.set_defaults(func=cmd_retry_missing)

    r = sub.add_parser("render")
    r.add_argument("--chat")
    r.add_argument("--month")
    r.set_defaults(func=cmd_render)

    s = sub.add_parser("serve")
    s.add_argument("--daemon", action="store_true")
    s.add_argument("--bind", default="127.0.0.1:8765")
    s.set_defaults(func=cmd_serve)

    se = sub.add_parser("search")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=50)
    se.add_argument("--chat")
    se.set_defaults(func=cmd_search)

    v = sub.add_parser("verify")
    v.add_argument("--no-write", action="store_true")
    v.add_argument("--snapshot", action="store_true")
    v.set_defaults(func=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    if not args.command:
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
