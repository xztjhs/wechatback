from __future__ import annotations

import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_root() -> Path:
    return Path(os.environ.get("WECHATBACK_DATA_ROOT", r"D:\WeChatData\xwechat_files"))


def _default_vault() -> Path:
    return Path(os.environ.get("WECHATBACK_VAULT", r"D:\WeChatBackVault"))


def _default_system_dir() -> Path:
    return Path(os.environ.get("WECHATBACK_SYSTEM_DIR", str(Path.home() / ".wechatback")))


def _default_ffmpeg() -> Path:
    configured = os.environ.get("WECHATBACK_FFMPEG")
    if configured:
        return Path(configured)
    found = shutil.which("ffmpeg")
    return Path(found) if found else Path("ffmpeg")


DEFAULT_DATA_ROOT = _default_data_root()
DEFAULT_VAULT = _default_vault()
DEFAULT_SYSTEM_DIR = _default_system_dir()
DEFAULT_FFMPEG = _default_ffmpeg()


@dataclass
class Config:
    data_root: Path = DEFAULT_DATA_ROOT
    account: str = ""
    vault: Path = DEFAULT_VAULT
    system_dir: Path = DEFAULT_SYSTEM_DIR
    ffmpeg: Path = DEFAULT_FFMPEG
    image_workers: int = 16
    copy_workers: int = 8
    inline: str = "thumb"          # thumb | full
    max_messages_per_note: int = 5000

    # derived
    secrets_dir: Path = field(init=False)
    work_dir: Path = field(init=False)
    db_dir: Path = field(init=False)
    archive_db: Path = field(init=False)
    logs_dir: Path = field(init=False)
    quarantine_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.data_root = Path(self.data_root)
        self.vault = Path(self.vault)
        self.system_dir = Path(self.system_dir)
        self.ffmpeg = Path(self.ffmpeg)
        self.secrets_dir = self.system_dir / "secrets"
        self.work_dir = self.system_dir / "work"
        self.db_dir = self.work_dir / "decrypted"
        self.archive_db = self.system_dir / "archive.db"
        self.logs_dir = self.system_dir / "logs"
        self.quarantine_dir = self.system_dir / "quarantine"

    @property
    def account_dir(self) -> Path | None:
        if self.account:
            p = self.data_root / self.account
            if p.is_dir():
                return p
            for child in self.data_root.iterdir():
                if child.is_dir() and child.name.startswith(self.account):
                    return child
        dirs = [p for p in self.data_root.iterdir() if p.is_dir() and (p / "db_storage").is_dir()]
        if not dirs:
            return None
        return max(dirs, key=lambda p: (p / "db_storage").stat().st_mtime)

    @property
    def wxid(self) -> str:
        if self.account:
            return self.account
        p = self.account_dir
        if not p:
            return ""
        return re.sub(r"_[0-9a-f]{4}$", "", p.name)

    @property
    def db_storage(self) -> Path | None:
        p = self.account_dir
        return p / "db_storage" if p else None

    @property
    def msg_dir(self) -> Path | None:
        p = self.account_dir
        return p / "msg" if p else None

    def ensure_dirs(self) -> None:
        for p in (
            self.vault,
            self.vault / "10_Chats",
            self.vault / "20_Media",
            self.vault / "20_Media" / "Images",
            self.vault / "20_Media" / "Thumbs",
            self.vault / "20_Media" / "Files",
            self.vault / "20_Media" / "Videos",
            self.vault / "30_Index",
            self.system_dir,
            self.secrets_dir,
            self.work_dir,
            self.logs_dir,
            self.quarantine_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


def load_config(path: Path | None = None) -> Config:
    system_dir = DEFAULT_SYSTEM_DIR
    cfg_path = Path(path) if path else system_dir / "config.toml"
    data: dict = {}
    if cfg_path.exists():
        with cfg_path.open("rb") as f:
            data = tomllib.load(f)

    vault = data.get("vault", {})
    wechat = data.get("wechat", {})
    render = data.get("render", {})
    media = data.get("media", {})

    cfg = Config(
        data_root=Path(wechat.get("data_root", DEFAULT_DATA_ROOT)),
        account=wechat.get("account", ""),
        vault=Path(vault.get("root", DEFAULT_VAULT)),
        system_dir=Path(vault.get("system_dir", DEFAULT_SYSTEM_DIR)),
        ffmpeg=Path(media.get("ffmpeg", DEFAULT_FFMPEG)),
        image_workers=int(media.get("workers", 8)),
        inline=render.get("inline", "thumb"),
        max_messages_per_note=int(render.get("max_messages_per_note", 5000)),
    )
    return cfg
