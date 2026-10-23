from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import struct
from pathlib import Path

from Crypto.Cipher import AES

from .config import Config

UIN_PATTERNS = [
    r"Tencent\xwechat\net\kvcomm\key_*.statistic",
    r"Tencent\xwechat\net_1\kvcomm\key_*.statistic",
    r"Tencent\xwechat\ilink\kvcomm\key_*.statistic",
    r"Tencent\xwechat\roam\ilink\kvcomm\key_*.statistic",
]


def discover_uin() -> int | None:
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return None
    for pat in UIN_PATTERNS:
        for f in glob.glob(os.path.join(appdata, pat)):
            m = re.search(r"key_(\d+)_", os.path.basename(f))
            if m:
                return int(m.group(1))
    return None


def derive_image_key(uin: int, wxid: str) -> tuple[bytes, int]:
    aes_key = hashlib.md5(f"{uin}{wxid}".encode()).hexdigest()[:16].encode()
    return aes_key, (uin & 0xFF)


def _sample_dat(cfg: Config) -> Path | None:
    msg = cfg.msg_dir
    if not msg:
        return None
    for p in msg.rglob("*.dat"):
        try:
            if p.stat().st_size > 2000:
                return p
        except OSError:
            continue
    return None


def verify_image_key(aes_key: bytes, xor_key: int, sample: Path) -> bool:
    data = sample.read_bytes()
    if len(data) < 64:
        return False
    sig, aes_size, xor_size = struct.unpack("<6sLLx", data[:15])
    if sig != b"\x07\x08V2\x08\x07":
        return False
    aes_len = aes_size + 16
    if 15 + aes_len > len(data):
        return False
    dec = AES.new(aes_key, AES.MODE_ECB).decrypt(data[15:15 + aes_len])
    # PKCS#7 unpad
    if dec:
        n = dec[-1]
        if 1 <= n <= 16 and dec[-n:] == bytes([n]) * n:
            dec = dec[:-n]
    head = dec[:12]
    return (
        head[:3] == b"\xff\xd8\xff"
        or head[:4] == b"\x89PNG"
        or head[:4] == b"GIF8"
        or head[:2] == b"BM"
        or head[:4] == b"wxgf"
        or head[:4] == b"RIFF"
    )


def load_or_create_image_key(cfg: Config) -> dict:
    cfg.ensure_dirs()
    cache = cfg.secrets_dir / "image_key.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            aes = bytes.fromhex(data["aes_hex"])
            sample = _sample_dat(cfg)
            if sample and verify_image_key(aes, int(data["xor_key"]), sample):
                return data
        except Exception:
            pass

    uin = discover_uin()
    if not uin:
        raise RuntimeError("未找到 kvcomm/key_<UIN>_*.statistic，无法派生图片密钥")
    aes_key, xor_key = derive_image_key(uin, cfg.wxid)
    sample = _sample_dat(cfg)
    if not sample:
        raise RuntimeError("未找到可验证的 .dat 样本")
    if not verify_image_key(aes_key, xor_key, sample):
        raise RuntimeError(
            f"图片密钥派生后校验失败：UIN={uin}，请确认账号 {cfg.wxid} 是否正确"
        )
    data = {
        "uin": uin,
        "wxid": cfg.wxid,
        "aes_hex": aes_key.hex(),
        "aes_ascii": aes_key.decode(),
        "xor_key": xor_key,
        "sample": str(sample),
    }
    cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def load_db_keys(cfg: Config) -> dict:
    p = cfg.secrets_dir / "db_keys.json"
    if not p.exists():
        raise RuntimeError(f"缺少数据库密钥文件：{p}")
    return json.loads(p.read_text(encoding="utf-8"))
