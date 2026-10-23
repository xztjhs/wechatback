# WeChatBack

把本机微信 4.x 的聊天记录导出为 **Obsidian 可直接打开、可直接搜索** 的本地知识库。

> 只读采集本机、本人账号的已登录数据；不抓包、不绕过登录、不上传云端。仓库内不含任何真实聊天数据、密钥或数据库。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)

## 它解决什么问题

微信自带的备份只能恢复到微信里，不能全文检索，也不能按客户、项目、时间做结构化分析。WeChatBack 把本地微信数据转换成一个 **可长期维护的 Obsidian Vault**：

- 文字：按会话 + 月份生成 Markdown，YAML frontmatter 兼容 Dataview；
- 图片：解密为 jpg/png/gif/webp/bmp，正文显示缩略图，点击看原图；
- 视频/文件：按 SHA-256 去重复制，链接直接可点；
- 增量：按会话水位 + 7 天回看，只处理新增消息；
- 可重建：SQLite `archive.db` 是事实层，Markdown 只是确定性投影。

## 实测结果

在一台 Windows 11 机器、微信 4.1.15.13 上完成全量导出（数据已脱敏）：

| 项目 | 结果 |
|---|---|
| 消息 | 1,663,631 条 |
| 会话 | 3,375 个 |
| 月份笔记 | 17,439 篇 |
| 图片（去重后） | 195,022 个 |
| 缩略图 | 186,622 张 |
| 视频（去重后） | 640 个 |
| 文件（去重后） | 3,061 个 |
| 媒体合计 | 198,723 个，约 52.8 GB |
| Vault 总大小 | 约 53 GB |
| 图片失败 | 1,482 张（0.64%，源 `.dat` 截断） |
| 源数据校验 | 100/100 文件未修改 |

全量耗时约 8.5 小时（含 36 万张图片解密、缩略图和 Markdown 渲染）。增量重扫 64,012 条近期消息约几分钟。

## 架构

```text
微信 4.x（只读）
  db_storage/*.db          msg/attach/**/*.dat       msg/video|file/**
        │                         │                        │
        │ 只读/解密副本             │ 直接只读                │ 直接只读
        ▼                         ▼                        ▼
  DB Decryptor              Image Decoder             Media Hasher
  SQLCipher4                AES + XOR + WXGF          SHA-256 去重
        │                         │                        │
        └──────────────┬──────────┴────────────┬───────────┘
                       ▼                       ▼
              archive.db（SQLite）        pending_media / state.db
                       │
                       ▼
              Obsidian Vault
              10_Chats / 20_Media / 30_Index
```

## 快速开始

### 1. 环境

- Windows 10/11
- 微信 4.x（`Weixin.exe`），保持登录
- Python 3.11+
- ffmpeg（处理 WXGF/HEVC 图片，可选但推荐）
- Obsidian（查看与检索）
- 首次提取密钥需要管理员权限

### 2. 安装

```powershell
git clone https://github.com/xztjhs/wechatback.git
cd wechatback

py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e .
```

> 数据库取钥/解密使用 MIT 协议的 [wcdb-key-tool](tools/wcdb-key-tool/README.md)，已放在 `tools/wcdb-key-tool/`，无需额外安装。

### 3. 配置

```powershell
mkdir "$env:USERPROFILE\.wechatback"
copy config.example.toml "$env:USERPROFILE\.wechatback\config.toml"
notepad "$env:USERPROFILE\.wechatback\config.toml"
```

至少修改：

```toml
[vault]
root = 'D:\WeChatBackVault'
system_dir = 'C:\Users\YOUR_NAME\.wechatback'

[wechat]
data_root = 'D:\WeChatData\xwechat_files'
```

也可以用环境变量覆盖：

| 变量 | 作用 |
|---|---|
| `WECHATBACK_DATA_ROOT` | 微信数据根目录 |
| `WECHATBACK_VAULT` | Obsidian 工作库 |
| `WECHATBACK_SYSTEM_DIR` | 密钥/归档/状态目录 |
| `WECHATBACK_BACKUP` | 备份根目录（SMB/NAS/移动硬盘） |
| `WECHATBACK_FFMPEG` | ffmpeg 路径 |

### 4. 先跑 20 条样本

```powershell
# 管理员 PowerShell，微信保持登录
wechatback doctor
powershell -ExecutionPolicy Bypass -File .\scripts\decrypt-db.ps1
wechatback image-key probe      # 抽样验证图片密钥

# 只导 20 条，确认 Vault 能打开
wechatback export --full --limit 20
```

确认 `10_Chats` 有笔记、图片能显示、链接能点开，再跑全量。

### 5. 全量导出

```powershell
wechatback export --full
```

### 6. 增量

```powershell
wechatback export --incremental
```

规则：每个会话保留水位，向前回看 7 天；新增分片、新会话自动纳入；按 `msg_id` upsert；Markdown 内容未变化时跳过重写。

## 发布到 NAS / 移动硬盘

设置备份根目录后，一条命令完成“增量导出 → 同步 Vault → 同步系统快照”：

```powershell
$env:WECHATBACK_BACKUP = '\\NAS\Backup\WeChatBack'
powershell -ExecutionPolicy Bypass -File .\scripts\publish.ps1
```

默认不复制密钥；如确实需要把密钥一起冷备，加 `-IncludeSecrets`，并确保目标盘加密。

只同步 Vault：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync-vault.ps1 -Backup '\\NAS\Backup\WeChatBack'
```

`sync-vault.ps1` 默认只复制新增/变化，不删除目标文件；需要严格镜像时加 `-Mirror`（会删除目标端多余文件）。

## Obsidian 设置

1. 打开本地 Vault（例如 `D:\WeChatBackVault`）。
2. 设置 → 文件与链接 → 新建链接格式选 **相对路径**。
3. 安装 Dataview（可选），打开 `30_Index/会话总览.md`。
4. 全局搜索 `Ctrl+Shift+F`。
5. 大 Vault 建议把 `20_Media/` 加入“排除文件”，图片仍能显示，索引会快很多。

> 不要把 40 万文件的 Vault 直接放在 SMB 上打开 Obsidian；元数据延迟会让启动索引卡住。实测 405,303 个文件 / 51.1 GiB 时不可用，改为“本地工作库 + NAS 冷备”后正常。

## 目录结构

```text
D:\WeChatBackVault\
├─ 10_Chats\        私聊/群聊/公众号，按会话/月分笔记
├─ 20_Media\
│  ├─ Images\       解密原图
│  ├─ Thumbs\       缩略图
│  ├─ Videos\       去重视频
│  └─ Files\        去重文件
├─ 30_Index\        Dataview 索引
└─ README.md

%USERPROFILE%\.wechatback\
├─ config.toml
├─ archive.db       事实层（SQLite）
├─ state.db         增量水位
├─ secrets\         密钥（绝不提交 Git）
├─ logs\
├─ work\            临时解密库
└─ quarantine\      失败样本
```

## 常用命令

```powershell
wechatback doctor                       # 环境自检
powershell -ExecutionPolicy Bypass -File .\scripts\decrypt-db.ps1  # 重新提取 DB key 并解密
wechatback image-key probe              # 验证图片密钥
wechatback export --full                # 全量
wechatback export --incremental         # 增量
wechatback export --reconcile           # 全量对账
wechatback media --skip-images          # 补视频/文件
wechatback retry-missing --kind video   # 重置缺失视频
wechatback retry-missing --kind file    # 重置缺失文件
wechatback render --chat <id> --month 2026-10
wechatback search "关键词"               # 命令行检索
wechatback serve                        # 本地媒体网关
wechatback verify                       # 验收
wechatback verify --no-write            # 只检查源数据未变
```

## 已知限制

1. **版本敏感**：微信 4.x 的数据库和 `.dat` 格式随版本变化；升级后先跑 `doctor` + 20 条/20 张样本。
2. **图片失败**：源 `.dat` 下载不完整时无法恢复；实测失败率 0.64%。
3. **视频/文件缺失**：微信本地没有下载原文件时只能保留消息占位；实测视频仅 3.70%、文件 28.04% 有本地副本。
4. **语音未导出**：正文显示 `[语音 Ns]`。
5. **WXGF 动图**：当前只取首帧为 PNG。
6. **需管理员权限**：提取 DB key 需要读取微信进程内存；不提供绕过登录或远程抓取能力。

## 安全与合规

- 仅用于导出**本人本机、本人账号**的聊天数据。
- 密钥、`archive.db`、导出 Vault、日志**一律不要提交 Git**；`.gitignore` 已默认排除。
- 导出的 Vault 包含他人聊天内容，请使用 BitLocker/VeraCrypt 或限制 SMB 权限。
- 不要将导出数据上传到网盘、公开仓库或第三方服务。
- 使用前请确认符合当地法律、微信服务条款及你所在组织的合规要求。

## 文档

- [方案设计](docs/01-方案设计.md) — 架构、格式、Obsidian 适配、去重、增量、安全
- [数据字典](docs/02-数据字典.md) — 微信 4.x 库表、消息类型、归档库、Markdown 结构
- [实施计划](docs/03-实施计划.md) — 阶段、交付物、验收标准、风险
- [运维手册](docs/04-运维手册.md) — 命令、计划任务、故障处理、版本升级
- [决策清单](docs/05-决策清单.md) — 关键取舍与最终选择
- [验证记录](docs/06-验证记录.md) — 密钥、样本、全量结果、Obsidian 检查
- [实战复盘](docs/07-实战复盘与经验.md) — 完整过程、关键参数、踩坑
- [CSDN 发布稿](docs/08-CSDN发布稿.md) — 项目介绍与实测过程

## 技术参考

- [技术笔记](skill/wechat-backup/references/technical-notes.md) — SQLCipher4、`.dat` V2、WXGF、媒体映射、SMB 陷阱
- [Skill](skill/wechat-backup/SKILL.md) — 可安装到 Cherry Studio / Claude 的运维技能

## License

[MIT](LICENSE)。仅用于个人数据备份；请勿用于侵犯他人隐私或未经授权的数据获取。
