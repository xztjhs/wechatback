# WeChatBack 技术参考

## 1. 微信 4.x 数据库

- 目录：`xwechat_files\<wxid>_<suffix>\db_storage`
- 加密：WCDB / SQLCipher4 变体（AES-256-CBC + HMAC-SHA512，page=4096，reserve=80）
- 消息库：`message\message_0..N.db`，另有 `message_fts.db`、`message_resource.db`
- 每会话表：`Msg_<md5(username)>`
- 每库 `Name2Id`：`real_sender_id` 只在当前库有效
- `contact.db`：`remark` > `nick_name` > `alias` > `username`
- `WCDB_CT_message_content = 4`：zstd 压缩

## 2. 密钥

### DB key

- 4.1+ 无明文 raw key；扫描 `com.Tencent.WCDB.Config.Cipher` 对象 + HMAC 校验。
- 本机 26/27 个库命中；`migrate\unspportmsg.db` 无 key 正常。

### 图片 key

```text
UIN 来源：%APPDATA%\Tencent\xwechat\{net,net_1,ilink,roam\ilink}\kvcomm\key_<UIN>_*.statistic
AES key = MD5(十进制UIN + wxid)[:16] 的 ASCII 字节
XOR key = UIN & 0xFF
```

- 不要用账号目录后缀、`head_imgs` 目录名当 UIN。
- 校验：PIL 完整打开整图，不能只看首块魔数。

## 3. `.dat` V2

```text
[15B header]
  07 08 56 32 08 07
  aes_size (LE u32)
  xor_size (LE u32)
  01
[AES-128-ECB, aes_size + 16 字节]
[PKCS#7 unpad]
[XOR, xor_size 字节]
```

- AES 段必须 `aes_size + 16`。
- PKCS#7 unpad 必须做。
- XOR 用 `UIN & 0xFF`，不要从文件尾反推。
- JPEG 落盘截断到最后一个 `FF D9`。
- 输出长度应与消息 XML `length` 一致。

## 4. WXGF / HEVC

- 解出载荷以 `wxgf` 开头。
- 扫描 `00 00 00 01` / `00 00 01`，从第一个起始码取到末尾。
- `ffmpeg -f hevc -i in.hevc -frames:v 1 out.png`。
- 动图当前取首帧。

## 5. 类型与媒体映射

```python
base_type = local_type & 0xFFFFFFFF
subtype   = local_type >> 32
```

| base_type | 含义 |
|---|---|
| 1 | 文本 |
| 3 | 图片 |
| 34 | 语音 |
| 43 | 视频 |
| 47 | 表情 |
| 48 | 位置/其他 |
| 49 | 富媒体（链接/文件/红包/引用…） |
| 10000 | 系统消息 |

| 类型 | 本地名 | 路径 |
|---|---|---|
| 图片 | `packed_info_data` 末尾 32 hex | `msg\attach\<md5(chat_id)>\<YYYY-MM>\Img\<md5>.dat` |
| 缩略图 | 同上 | `<md5>_t.dat` |
| 高清 | 同上 | `<md5>_h.dat` |
| 视频 | XML `md5`/`originsourcemd5` → hardlink；兜底 `(month,length)` | `msg\video\<YYYY-MM>\<file>.mp4` |
| 文件 | XML `md5` → hardlink；兜底 `msg\file\<month>\<title>` | 原文件名 |

`hardlink.db`：

- `dir2id`：rowid → chat hash 或 `YYYY-MM`
- 图片 `dir1=chat_hash, dir2=month`
- 视频/文件 `dir1=month, dir2=0`

## 6. 归档库

`archive.db`（SQLite）：

- `messages`：`msg_id`、`chat_id`、`ts`、`sender_name`、`type`、`content`、`media_id`、`media_key`、`source_status`
- `media`：`media_id`(sha256)、`kind`、`export_path`、`thumb_path`、`source_path`
- `pending_media`：`source_key`、`kind`、`status`（pending/done/failed/missing）
- `chats`：会话元数据

`state.db`：

- `chat_state(chat_id,last_ts)`：增量水位
- `runs`：运行记录

## 7. SMB 陷阱

- `Path.exists()` 可能命中旧缓存 → **不要用它跳过写入**。
- 图片/缩略图/视频/文件一律写/复制；临时名加 `pid + thread_id`。
- 40 万文件不要用 Obsidian 直接打开 NAS；用本地工作库。
- 文件链接含空格/括号用 `<...>`。

## 8. 常见失败

| 现象 | 原因 | 修复 |
|---|---|---|
| 首块像图、整图错 | AES 少 16B / unpad 错 | `aes_size+16` + unpad |
| 图片全失败 | UIN 错 | 从 kvcomm 取 UIN，PIL 校验 |
| `Permission denied .tmp` | 并发同名临时文件 | 唯一临时名 |
| `truncated dat` | 源文件不完整 | quarantine，不阻塞 |
| media 行有、文件无 | SMB exists 缓存 | 重置 pending 补跑 |
| 缩略图破图 | 旧代码跳过 thumb 写入 | 从原图补生成 |
| 文件链接断在 `)` | Markdown 括号 | 目标 `<...>` |
| Obsidian 卡 | NAS SMB + 40 万文件 | 本地工作库 |
| 视频/文件缺失 | 微信未下载 | 打开聊天下载后 retry-missing |
