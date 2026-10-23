# wcdb-key-tool (Windows)

Third-party MIT-licensed helper used to extract WeChat 4.x database keys and decrypt the databases.

- Upstream: https://github.com/TANGandXUE/wcdb-key-tool
- Copyright: 2025 CloudDreamAI / TANGandXUE
- License: MIT (see `LICENSE` in this folder)
- Vendored file: `wcdb_key_tool_windows.py`

## What it does

1. Reads the `Config.Cipher` object from the running `Weixin.exe` process (read-only).
2. Verifies each candidate key against the database HMAC.
3. Decrypts each SQLCipher4 database page into a standard SQLite file.

It does not modify the WeChat process or the source database.

## Usage

From an elevated PowerShell, with WeChat 4.x running and logged in:

```powershell
python tools\wcdb-key-tool\wcdb_key_tool_windows.py extract `
  --db-dir "<WECHAT_DATA_ROOT>\<account>\db_storage" `
  --output "%USERPROFILE%\.wechatback\secrets\db_keys.json" `
  --decrypt
```

Run it from `%USERPROFILE%\.wechatback\work` so the decrypted database tree lands in `work\decrypted`, or use the wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\decrypt-db.ps1
```

If the script reports `0 keys`, make sure WeChat is running, you are logged in, and the shell is elevated.

## Security

Only use this on your own machine and your own account. Never share `db_keys.json` or the decrypted databases.
