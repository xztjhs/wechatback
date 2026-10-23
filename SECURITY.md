# Security Policy

## Scope

WeChatBack reads a local, already-logged-in WeChat 4.x data directory on Windows. It does not provide remote access, login bypass, or network interception.

## Do not publish

Never attach any of the following to an issue, pull request, discussion, or gist:

- `archive.db`, `state.db`, or any WeChat database file
- `secrets/db_keys.json`, `secrets/image_key.json`, or any key material
- exported notes, images, videos, files, or `messages.jsonl`
- logs containing local paths, account identifiers, or chat metadata

Use synthetic examples when reporting a bug.

## Reporting

Open a minimal issue with:

- WeChat version and Windows version
- the command you ran
- the error message with paths and account identifiers removed
- whether the source data was modified (it should never be)

## Data handling

The exported vault contains private conversations and media. Store it on an encrypted disk, restrict SMB/NAS share permissions, and do not sync it to public cloud storage.
