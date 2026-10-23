---
name: wechat-backup
description: Back up/export WeChat 4.x chat history (text, images, thumbnails, videos, files) into an Obsidian vault, run incremental updates, repair missing media, and publish to a local or NAS backup root. Use when the user asks to export/back up WeChat, update the Obsidian archive, check WeChat backup health, or repair missing images/videos/files.
---

# WeChatBack

把本机微信 4.x 的聊天记录导出为 Obsidian 可直接打开、可搜索的本地知识库，并发布到本地磁盘或 NAS。

## 路径约定

所有路径通过环境变量或 `config.toml` 配置，不要写死在脚本里：

| 变量 | 作用 | 示例 |
|---|---|---|
| `WECHATBACK_REPO` | 代码仓库 | `D:\Tools\WeChatBack` |
| `WECHATBACK_DATA_ROOT` | 微信数据根目录 | `D:\WeChatData\xwechat_files` |
| `WECHATBACK_VAULT` | Obsidian 工作库 | `D:\WeChatBackVault` |
| `WECHATBACK_SYSTEM_DIR` | 密钥/归档/状态 | `%USERPROFILE%\.wechatback` |
| `WECHATBACK_BACKUP` | 备份根目录 | `\\NAS\Backup\WeChatBack` |
| `WECHATBACK_FFMPEG` | ffmpeg 路径 | `C:\Tools\ffmpeg\bin\ffmpeg.exe` |

> 工作库放本地磁盘；NAS 用于冷备。不要用 Obsidian 直接打开 40 万文件的 SMB 目录。

## 快速命令

```powershell
cd $env:WECHATBACK_REPO

# 自检
.venv\Scripts\python.exe -m wechatback.cli doctor

# 增量 + 同步发布（推荐）
powershell -ExecutionPolicy Bypass -File .\scripts\publish.ps1

# 只增量（不同步）
.venv\Scripts\python.exe -m wechatback.cli export --incremental

# 全量（仅在首次或结构大改时）
.venv\Scripts\python.exe -m wechatback.cli export --full

# 微信里补下载视频/文件后
.venv\Scripts\python.exe -m wechatback.cli retry-missing --kind video
.venv\Scripts\python.exe -m wechatback.cli retry-missing --kind file
.venv\Scripts\python.exe -m wechatback.cli media --skip-images

# 验收
.venv\Scripts\python.exe -m wechatback.cli verify --no-write
```

## 安全规则

1. 只处理本人本机、本人账号；不读取他人账号。
2. 源目录只读；不写、不移动、不删除 `xwechat_files`。
3. 不保存原始镜像；解密库只放临时目录，跑完可删。
4. 密钥只在 `%USERPROFILE%\.wechatback\secrets`；不要把密钥提交 Git 或分享 Vault。
5. 导出结果含他人聊天内容；分享前脱敏。
6. `sync-system.ps1` 默认不复制密钥；需要冷备密钥时显式加 `-IncludeSecrets`。

## 技术要点（必须知道）

- 图片 AES key = `MD5(UIN + wxid)[:16]` 的 ASCII；UIN 来自
  `%APPDATA%\Tencent\xwechat\{net,net_1,ilink,roam\ilink}\kvcomm\key_<UIN>_*.statistic`。
- 图片 XOR key = `UIN & 0xFF`。
- `.dat` V2：15B 头 + `(aes_size + 16)` AES-128-ECB + PKCS#7 unpad + `xor_size` XOR。
- `local_type` 是位打包：`base_type = local_type & 0xFFFFFFFF`，`subtype = local_type >> 32`。
- 图片/视频本地文件名来自 `packed_info_data` 末尾 32 位 hex；文件来自 XML `md5` + `title`。
- WXGF 是 HEVC：扫描起始码后用 ffmpeg 取首帧。
- SMB 上不要用 `Path.exists()` 跳过写入（会命中旧缓存）；一律写/复制。
- Obsidian 文件链接含空格/括号时用 `<...>` 包裹。

详细参数与失败模式见 `references/technical-notes.md`，完整复盘见仓库 `docs/07-实战复盘与经验.md`。

## 增量逻辑

- `state.db` 保存 per-chat `last_ts`；本次查询 `create_time >= last_ts - 7 天`。
- 新分片（`message_7.db` 等）、新会话自动纳入。
- 撤回/编辑更新归档，不删除历史。
- 渲染内容未变化时跳过写入。

## 发布到 NAS / 移动硬盘

```powershell
# 工作库 → 备份根目录（只复制新增/变化，不删除）
powershell -ExecutionPolicy Bypass -File .\scripts\sync-vault.ps1 -Backup $env:WECHATBACK_BACKUP

# archive.db/state.db/日志 → <备份根>\90_System
powershell -ExecutionPolicy Bypass -File .\scripts\sync-system.ps1 -Dest $env:WECHATBACK_BACKUP
```

`publish.ps1` 会依次执行增量、同步 Vault、同步系统快照。

## 排障

| 现象 | 处理 |
|---|---|
| `doctor` 找不到微信 | 启动并登录 `Weixin.exe`（4.x） |
| DB key 0 命中 | 管理员运行；确认微信版本；重跑 `key refresh` |
| 图片首块对、整图错 | 检查 AES 段是否 `aes_size + 16`、是否 unpad |
| 图片整批失败 | 检查 UIN 是否来自 `kvcomm`，用 PIL 完整校验 |
| `media` 行有、文件无 | SMB `exists()` 缓存；重置 `pending` 后 `media` 补跑 |
| 缩略图破图 | 从原图补生成缩略图 |
| 文件链接断在 `)` | 链接目标用 `<...>` |
| Obsidian 卡 | 打开本地工作库，不要打开 NAS |
| 视频/文件缺失 | 微信本地没下载；打开聊天触发下载后 `retry-missing` |

## 文件地图

```text
D:\WeChatBackVault\
├─ 10_Chats\     私聊/群聊/公众号，按会话/月
├─ 20_Media\     Images / Thumbs / Videos / Files
├─ 30_Index\     Dataview 索引
└─ README.md

<备份根>\
├─ 10_Chats\ 20_Media\ 30_Index\   # 发布结果
└─ 90_System\                      # config / db / logs（可选 secrets）
```

## 验收

```powershell
.venv\Scripts\python.exe -m wechatback.cli verify --no-write
```

重点看：

- `messages` / `chats` 计数；
- `pending_media` 无大量 `pending`；
- Vault 内图片 `.dat` = 0；
- 抽样笔记 frontmatter、消息 ID、日期标题；
- 抽样媒体链接目标存在；
- `source_snapshot.json` changed=0。
