# wa-digest-v2

WhatsApp Beta Digest v2 — design + workflow + infra scripts.

## Why this repo exists

This repo is a **temporary delivery channel** while Bender's Google Drive sync is offline. The canonical path for these artefacts is:

```
G:\Shared drives\Rolla\members\borec-sven\outputs\bender whatsapp digest\v2-design\
```

Once Bender's DriveFS daemon is restored, contents will be mirrored there and this repo will be transferred to `Rolla-Health-Fitness/wa-digest-v2`.

## Contents

| File | What | Status |
|---|---|---|
| `architecture.md` | v0.2 design doc | approved-for-implementation, all v0.1 unblockers resolved |
| `workflow.json` | n8n workflow skeleton (29 nodes) | JSON-valid, importable into n8n, credentials wired by id |
| `setup-pgvector.sh` | bash bootstrap for pgvector + schema | idempotent, run on n8n host Tue May 26 morning |

## Owner / operator

- **Designer / maintainer:** Bender
- **Operator / runtime owner:** Sven Borec
- **Target shadow live:** 2026-05-27 (Wed)
- **Target cutover decision:** 2026-06-05 (Fri)
- **Target live cutover:** 2026-06-08 (Mon)

## Delivery context

Delivered 2026-05-25 via GitHub fallback after DriveFS daemon for `bender@rolla.app` was found dead:

```
$ cat ~/Library/Application Support/Google/DriveFS/pid.txt
1240
$ ps -p 1240
  PID USER COMMAND
  (no row)              # daemon not alive
```

Drive reads were stale ≥6 days; writes never propagated. `#bender-requests` ticket filed same day asking Igor / infra owner to restart DriveFS + re-auth `bender@rolla.app`.

Until the daemon is back, this repo is the source of truth for v2 design artefacts.
