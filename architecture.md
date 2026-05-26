---
title: WhatsApp Beta Digest v2 — Architecture
status: approved-for-implementation
version: 0.3
author: Bender (designer/maintainer)
operator: Sven Borec (runtime owner)
created: 2026-05-22
last-updated: 2026-05-26
review-by: 2026-05-26 (Tue morning) — Sven's 6-point review (channel, cron, §10/§16/§17 drift) applied
target-shadow-live: 2026-05-27 (Wed)
target-cutover-decision: 2026-06-05 (Fri)
target-live-cutover: 2026-06-08 (Mon)
changelog:
  - "v0.3 (2026-05-26) — Sven 6-point review applied: (1) output channel re-routed to #whatsapp-beta-feed (C0ANXU0NKFB) with [v2 SHADOW] prefix (no new channel); (2) cron switched to `30 2 * * *` in container UTC-4 to fire at 08:30 CEST without container restart; (3) §10 rewritten to dedicated `n8n-postgres-1` container (image pgvector/pgvector:pg16, network ollama_llm, volume n8n_pgvector_data) — verified live by Sven 2026-05-26 (pgvector 0.8.2, 5 tables + 4 ivfflat indices, vector smoke-test passed); (4) §16 OPE-379 verified (Linear: Done 2026-05-25); (5) §17 repo path locked to bender-boop/wa-digest-v2; (6) §7.1 heading channel ref updated."
  - "v0.2 (2026-05-25) — All §14 unblockers resolved. Roadmap = Linear projects (no separate Doc). Credential names + IDs locked in. Status flipped to approved-for-implementation."
  - "v0.1 (2026-05-22) — Initial draft for Sven review."
---

## 1. Goal

Daily digest that *interprets* WhatsApp beta tester reports (text + screenshots + videos + voice notes), *cross-checks* them against Linear issues, the Rolla codebase, the roadmap, and Zendesk tickets, and outputs a ≤300-word top-level Slack post + long-form thread reply — with *zero invented references*.

v1 of the digest (`kj0v8HDESttbVI2t`) is text-only and treats `[PHOTO …]` markers as strings. v2 *reads* the media and grounds every claim in a verifiable source.

## 2. Posture

- Parallel n8n workflow — *not* a modification of `kj0v8HDESttbVI2t`. v1 keeps running untouched during shadow.
- Shadow mode posts to `#whatsapp-beta-feed` (`C0ANXU0NKFB`) with `[v2 SHADOW]` prefix on every message — Sven-only review first 3 days. `@whatsapp_bot` already has post access. Prefix is dropped at cutover; no new channel is required.
- Cutover to regular feed only if all 6 cutover criteria (§9) hold for 5 consecutive weekdays.
- Sven personally watches every output for the first 3 days; expect daily prompt/threshold tuning.

## 3. Architecture Overview

```
                     ┌────────────────────────────────────────┐
                     │  TRIGGER                               │
                     │  • cron 08:30 Europe/Sarajevo          │
                     │  • POST /webhook/wa-digest-v2-now      │
                     └──────────────────┬─────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 1 — MULTIMODAL ENRICHMENT                                    │
│                                                                     │
│  Drive → fetch messages_<date>.json + media folder                  │
│         (folder id: 18zK9AWntj57Jp1fr0TLr78jCZGMHx-AO)              │
│                                                                     │
│  For each message:                                                  │
│    • text-only       → pass through                                 │
│    • photo_*.jpg     → qwen2.5vl:7b @ rolla-3080  → observed_content│
│    • video_*.mp4     → ffmpeg keyframes (0.5fps, max 10)            │
│                        → qwen2.5vl:7b batch         → observed_content│
│    • audio_*.ogg     → whisper.cpp medium          → folded into text│
└──────────────────┬──────────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 2 — CROSS-REFERENCE                                          │
│                                                                     │
│  embed(text + observed_content) with bge-m3                         │
│                                                                     │
│  pgvector queries against:                                          │
│    • linear_index    (nightly refresh, Linear API — issues + projects)│
│    • codebase_index  (Sunday 03:00 refresh, 9 repos via gh PAT)     │
│    • (no separate roadmap_index — Linear projects ARE the roadmap)  │
│                                                                     │
│  Zendesk dedupe (parallel):                                         │
│    • Stage 1 (hard) — phone match, ±48h          → auto-merge       │
│    • Stage 2 (soft) — bge-m3 cosine ≥0.82, ±7d   → flag for review  │
└──────────────────┬──────────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 3 — ASSEMBLY + HALLUCINATION HARD GATE                       │
│                                                                     │
│  LLM (gemma-4 or llama3.1:8b) → drafts ≤300-word digest             │
│     prompt: "If unsure, say unsure. Never invent IDs or paths."     │
│                                                                     │
│  Per-reference validation (regex-extract, then verify in match set) │
│    • Linear IDs   → must exist in stage-2 linear matches            │
│    • File paths   → must exist in stage-2 codebase matches          │
│    • Roadmap items→ must exist in stage-2 roadmap matches           │
│  → fails: drop that reference line                                  │
│                                                                     │
│  Whole-digest gate:                                                 │
│    • if >20% of cross-ref lines dropped → DO NOT POST               │
│    • post stub + debug payload in thread                            │
└──────────────────┬──────────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  OUTPUT                                                             │
│                                                                     │
│  • Slack top-level → #whatsapp-beta-feed [v2 SHADOW] (≤300 words)   │
│  • Slack thread reply → long-form per-message detail                │
│  • Drive write → outputs/bender whatsapp digest/digest_<date>.json  │
│                  (full audit payload)                               │
│  • Drive write → outputs/bender whatsapp digest/digest_<date>.failed│
│                  .json (if hard gate triggered)                     │
└─────────────────────────────────────────────────────────────────────┘
```

## 4. Stage 1 — Multimodal Enrichment

### 4.1 Photo handler

- Model: `qwen2.5vl:7b` on rolla-3080 (RTX 3080 10GB) via Ollama
- Quantization: Q4_K_M (~5GB VRAM, ~30GB context headroom)
- Pull: `ollama pull qwen2.5vl:7b` (Sven, Sunday May 24)
- Prompt template (per-image):
  ```
  You are analyzing a screenshot from a beta tester of the Rolla
  fitness mobile app. Describe what is visible in 2-4 short sentences.
  Focus on: visible UI elements, error messages, app state, numeric
  values displayed, and any sign of malfunction. Do NOT speculate
  about cause. If the image is unrelated to the app, say "unrelated".
  ```
- Output: `observed_content` string attached to the message object
- Timeout: 30s per image; on timeout, fall back to `[VLM timeout — see raw image at <path>]`

### 4.2 Video handler

- Tooling: ffmpeg
- Keyframe extraction:
  ```bash
  ffmpeg -i video_<sender>_<ts>.mp4 \
         -vf "fps=0.5,scale=1280:-2" \
         -frames:v 10 \
         -q:v 4 \
         frame_%02d.jpg
  ```
- Dedupe near-identical frames pre-VLM: pHash distance threshold 8 (perceptual hash); collapse to unique frames
- Batch through `qwen2.5vl:7b` with prompt extended: _"These are sequential frames from a short screen recording — describe the sequence as a flow."_
- Skip audio track entirely for v1 (most beta video is silent screen capture)

### 4.3 Audio handler

- Tooling: `whisper.cpp` medium model on rolla-3080 (or n8n host, fits in CPU)
- Output folded into message `text` field with prefix `[AUDIO transcript: …]`
- Language auto-detect; BS/HR/SL transcribed natively (no translation)

## 5. Stage 2 — Cross-Reference

### 5.1 Embedding model

- `bge-m3` (same as Dify KB to keep stack consistent)
- Dimension: 1024
- Multilingual native — handles BS/HR/SL/EN without preprocessing

### 5.2 Linear index (issues + projects)

**Confirmed v0.2:** Linear is the single source of truth for both _issues_ (tactical) AND _projects_ (roadmap layer). No separate roadmap doc exists — `index_linear.py` writes to two tables in the same nightly run.

- Refresh: nightly 02:00 via Linear API GraphQL (single workflow, two destination tables)
- n8n credential reference: `Linear - Rolla (Bender v2 digest)` (id `iSfZn3cLB3gqd9gM`, type `linearApi`)
- Issues query: all non-cancelled issues across all teams visible to the PAT (OPE, PE, MAR, etc.)
- Projects query: all active + planned projects across the same scope (cancelled/completed excluded)
- Schema — `linear_index` (issues):
  ```sql
  CREATE TABLE linear_index (
    issue_id        text PRIMARY KEY,              -- "OPE-229"
    title           text NOT NULL,
    description     text,
    state           text,                          -- "In Progress", "Done", etc.
    priority        int,
    labels          text[],
    project_id      text,                          -- FK to linear_projects_index (nullable)
    updated_at      timestamptz,
    embedding       vector(1024)
  );
  CREATE INDEX linear_index_embedding_idx
    ON linear_index USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
  ```
- Schema — `linear_projects_index` (roadmap):
  ```sql
  CREATE TABLE linear_projects_index (
    project_id      text PRIMARY KEY,              -- Linear project UUID
    name            text NOT NULL,                 -- "Sleep v2"
    description     text,
    state           text,                          -- "started", "planned", "paused"
    lead_email      text,
    target_date     date,
    teams           text[],                        -- ["OPE", "PE"]
    updated_at      timestamptz,
    embedding       vector(1024)
  );
  CREATE INDEX linear_projects_index_embedding_idx
    ON linear_projects_index USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);
  ```
- Embedded text (issues):   `title + "\n\n" + description`
- Embedded text (projects): `name + "\n\n" + description`
- Stage 2 emits TWO match arrays in `verified_matches`: `linear[]` (issues) and `roadmap[]` (projects). Stage 3 hard gate validates both arrays identically.

### 5.3 Codebase index

- Refresh: Sunday 03:00 via GitHub API + local clone
- Scope: 9 repos accessible via Bender's GitHub PAT
  - Private: `docs`, `wl-rolla-mobile`, `rolla-sdk`
  - Public: `rolla-sdk-release-{android,ios,react-native,test-android,test-ios}`, `rolla-sdk-documentation`
- Per-file extraction:
  - Path (e.g. `app/sleep/Scorer.kt`)
  - First 30 lines of file (captures top-of-file docstring)
  - Function/class signatures (regex per-language: kotlin, swift, typescript, javascript, dart, python, markdown)
  - Last-commit SHA + message + date
- Schema:
  ```sql
  CREATE TABLE codebase_index (
    repo            text NOT NULL,
    path            text NOT NULL,
    docstring       text,
    signatures      text,
    last_commit_sha text,
    last_commit_msg text,
    last_commit_at  timestamptz,
    embedding       vector(1024),
    PRIMARY KEY (repo, path)
  );
  CREATE INDEX codebase_index_embedding_idx
    ON codebase_index USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 200);
  ```
- Embedded text: `repo + "/" + path + "\n\n" + docstring + "\n\n" + signatures`
- Backend bugs: if a report's top match is in a repo Bender can't see (would require widened PAT), emit `→ matches: needs backend reviewer` per Sven's spec

### 5.4 Roadmap source — RESOLVED v0.2

**Decision (Sven, 2026-05-25):** No separate roadmap document exists. Linear projects are the roadmap layer.

- Implementation lives entirely in §5.2 (`linear_projects_index`)
- `index_linear.py` does both issue and project indexing in one nightly run
- Stage 3 hard gate validates roadmap mentions against `linear_projects_index.name` matches surfaced in Stage 2
- The previously-spec'd standalone `roadmap_index` table from v0.1 is **removed** — do not create it during migration
- v1.1 candidate: if/when a richer roadmap doc emerges, layer it ALONGSIDE the projects index, do not replace

### 5.5 Zendesk dedupe

- *Stage 1 — hard match*:
  - Query Zendesk Search API for tickets from the same tester (phone first, email fallback) within ±48h
  - If hit, attach `dedupe: { type: "hard", zendesk_id: "ZD-12345" }` and mark for *silent merge* in the digest
- *Stage 2 — soft match*:
  - Embed all open Zendesk tickets nightly into `zendesk_index` (same schema as Linear)
  - Query bge-m3 cosine ≥0.82 within ±7d window, any sender
  - Attach `dedupe: { type: "soft", zendesk_id: "ZD-12345", confidence: 0.87 }` and surface in digest for human review

## 6. Stage 3 — Assembly + Hallucination Hard Gate

### 6.1 LLM call

- Model: `gemma2:9b` first attempt (better instruction-following at this size class), fallback `llama3.1:8b` if gemma struggles with the schema
- Prompt skeleton (full template in `prompts/digest-assembly.txt`):
  ```
  You are drafting a daily digest of WhatsApp beta tester feedback for Rolla.

  RULES:
  • Top-level digest MUST be ≤300 words.
  • Sections: Volume / Top issues / Praise / Suggested follow-ups / High-urgency threads / Codebase + roadmap cross-check
  • Every Linear ID, file path, and roadmap item you mention MUST come from the
    provided "verified_matches" block. Do not invent references.
  • If you are unsure about a reference, write "(unverified)" — do not guess.
  • BS/HR/SL/EN messages are equal. Do not translate; quote inline if useful.

  INPUT:
  <messages_enriched_json>
  <verified_matches_json>
  ```

### 6.2 Hard gate — per-reference validation

After the LLM produces output, run a regex pass:

| Pattern | Source of truth | Action on fail |
|---|---|---|
| `[A-Z]+-\d+` (e.g. OPE-229) | `verified_matches.linear[].issue_id` | drop the entire reference line |
| File path with `.` extension or directory `/` | `verified_matches.codebase[].path` | drop the entire reference line |
| Roadmap item names (Linear project names) | `verified_matches.roadmap[].name` (sourced from `linear_projects_index`) | drop the entire reference line |

### 6.3 Hard gate — whole-digest threshold

- Count: total cross-reference lines emitted, total dropped
- If `dropped / total > 0.20` → *do not post the LLM output*
- Instead post stub:
  ```
  ⚠️ Digest 2026-MM-DD failed validation (X% references unverified).
  Full debug payload in thread.
  ```
- Drop full debug payload as thread reply with: original LLM output, dropped references, and `verified_matches` for inspection
- Write `outputs/bender whatsapp digest/digest_<date>.failed.json`

### 6.4 Thresholds (configurable in n8n env vars)

| Env var | Default | Meaning |
|---|---|---|
| `VLM_TIMEOUT_S` | 30 | Per-image VLM timeout |
| `EMBED_COSINE_LINEAR` | 0.75 | Min cosine for Linear match |
| `EMBED_COSINE_CODEBASE` | 0.70 | Min cosine for codebase match |
| `EMBED_COSINE_ZENDESK_SOFT` | 0.82 | Soft Zendesk dedupe threshold |
| `HARD_GATE_DROP_PCT` | 0.20 | Whole-digest fail threshold |
| `DIGEST_MAX_WORDS` | 300 | Top-level word cap (enforced post-LLM) |

## 7. Output Format

### 7.1 Slack top-level (`#whatsapp-beta-feed` with `[v2 SHADOW]` prefix, ≤300 words)

```
*WhatsApp Beta Digest — 2026-05-22*
Volume: 13 messages (7 user, 6 team) · BS 10 / EN 3

*Top issues (3)*
1. Sleep score not updating after Garmin sync (3 reports)
   → matches OPE-XXX (In Progress) | code: wl-rolla-mobile/app/sleep/Scorer.kt:142 | roadmap: yes (Q3)
2. ...

*Praise (1)*
...

*High-urgency (1)*
...

*Suggested follow-ups (2)*
...

_See thread for per-message detail._
```

### 7.2 Slack thread reply

- Long-form per-message JSON pretty-printed
- Includes: original text, observed_content, verified matches with confidence scores, dedupe status

### 7.3 Drive artefacts

- `outputs/bender whatsapp digest/digest_<date>.json` — full audit payload
- `outputs/bender whatsapp digest/digest_<date>.failed.json` — only on hard gate trigger

## 8. Repo Scope (v1)

In-scope (9 repos via Bender PAT):
- `Rolla-Health-Fitness/docs`
- `Rolla-Health-Fitness/wl-rolla-mobile` ← most tester reports
- `Rolla-Health-Fitness/rolla-sdk`
- `Rolla-Health-Fitness/rolla-sdk-release-{android, ios, react-native, test-android, test-ios}`
- `Rolla-Health-Fitness/rolla-sdk-documentation`

Out-of-scope for v1 (backend bugs flagged `→ matches: needs backend reviewer`):
- Backend services
- n8n workflow source
- Internal admin tools
- Zendesk macros / source

v1.1 candidate: widen PAT scope to include backend repos once shadow proves out.

## 9. Cutover Criteria (per Sven, all hold 5 consecutive weekdays)

1. Digest fires at 08:30 *and* on every webhook trigger — no missed runs
2. Every screenshot/video in `messages_<date>.json` is *looked at* by the VLM, with content surfacing in the digest when relevant — not just `[PHOTO …]` markers echoed through
3. Codebase + roadmap cross-check line appears for ≥80% of substantive reports.
   Substantive = `intent in {support, other}` AND (`urgency != low` OR `text length > 80`)
4. *Zero* hallucinated Linear IDs, file paths, or roadmap items. If unsure, says unsure
5. BS/HR/SL messages handled identically to English (multilingual embedder + multilingual VLM make this near-free)
6. Digest stays ≤300 words at top level. Long detail in thread, not the top post

## 10. pgvector Setup — LOCKED v0.3 (verified live 2026-05-26)

**Reality check (v0.3):** n8n's own metadata store on `10.10.103.200` is *SQLite*, not Postgres. v0.1/v0.2 wording that implied "install pgvector into n8n's Postgres" is wrong and has been removed. The v2 indices live in a **dedicated** Postgres+pgvector container that is parallel to n8n.

### 10.1 Deployed container (LOCKED — DO NOT REPROVISION)

| Field | Value |
|---|---|
| Container name | `n8n-postgres-1` |
| Image | `pgvector/pgvector:pg16` |
| Docker network | `ollama_llm` (shared with n8n; DNS resolves n8n=172.20.0.4 ↔ n8n-postgres-1=172.20.0.5) |
| Volume | `n8n_pgvector_data` |
| pgvector version | 0.8.2 |
| Password storage | `~/.secrets/n8n-pgvector.env` on the n8n host (chmod 600) — *never* in workflow.json, never in this repo |
| n8n credential | `Postgres - n8n-pgvector (Bender v2 digest)` (pending UI wiring as of 2026-05-26; not blocking shadow) |

### 10.2 Bootstrap (idempotent — script in this repo)

`setup-pgvector.sh` does everything in one shot: extensions (`vector` + `pgcrypto`), 5 tables, 4 ivfflat indices, drops legacy `roadmap_index` if present. Safe to re-run.

```bash
# Default container name (n8n-postgres-1):
bash setup-pgvector.sh

# Override (different host or container name):
PG_CONTAINER=my-postgres bash setup-pgvector.sh
```

### 10.3 Live verification (Sven, 2026-05-26 morning)

- ✅ pgvector 0.8.2 active
- ✅ 5 tables present: `linear_index`, `linear_projects_index`, `codebase_index`, `zendesk_index`, `digest_runs`
- ✅ 4 ivfflat indices present (vector_cosine_ops, lists per table size — see 10.4 below)
- ✅ vector type smoke-tested: `<->` L2 distance returned `1.0` as expected on the orthogonal-vector probe
- ✅ Docker DNS resolves both ways (n8n ↔ n8n-postgres-1) on the `ollama_llm` network
- ✅ n8n / Outline / Dify all still healthy after deploy (200 / 200 / 307)

### 10.4 Schema (created by `setup-pgvector.sh`)

Schema definition (reference — `setup-pgvector.sh` is the canonical source):

```sql
-- v0.3 schema (verified live 2026-05-26)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS linear_index (
  issue_id        text PRIMARY KEY,
  title           text NOT NULL,
  description     text,
  state           text,
  priority        int,
  labels          text[],
  project_id      text,                          -- FK soft-ref to linear_projects_index
  updated_at      timestamptz,
  embedding       vector(1024)
);
CREATE INDEX IF NOT EXISTS linear_index_embedding_idx
  ON linear_index USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- Linear projects = roadmap layer (v0.2: replaces standalone roadmap_index)
CREATE TABLE IF NOT EXISTS linear_projects_index (
  project_id      text PRIMARY KEY,
  name            text NOT NULL,
  description     text,
  state           text,                          -- 'started' | 'planned' | 'paused'
  lead_email      text,
  target_date     date,
  teams           text[],
  updated_at      timestamptz,
  embedding       vector(1024)
);
CREATE INDEX IF NOT EXISTS linear_projects_index_embedding_idx
  ON linear_projects_index USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 50);

CREATE TABLE IF NOT EXISTS codebase_index (
  repo            text NOT NULL,
  path            text NOT NULL,
  docstring       text,
  signatures      text,
  last_commit_sha text,
  last_commit_msg text,
  last_commit_at  timestamptz,
  embedding       vector(1024),
  PRIMARY KEY (repo, path)
);
CREATE INDEX IF NOT EXISTS codebase_index_embedding_idx
  ON codebase_index USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 200);

-- NOTE v0.2: standalone roadmap_index removed. Linear projects ARE the roadmap.

CREATE TABLE IF NOT EXISTS zendesk_index (
  ticket_id       text PRIMARY KEY,
  subject         text NOT NULL,
  body            text,
  status          text,
  requester_phone text,
  requester_email text,
  created_at      timestamptz,
  updated_at      timestamptz,
  embedding       vector(1024)
);
CREATE INDEX IF NOT EXISTS zendesk_index_embedding_idx
  ON zendesk_index USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- Audit / observability
CREATE TABLE IF NOT EXISTS digest_runs (
  run_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_at          timestamptz NOT NULL DEFAULT now(),
  trigger         text NOT NULL,                    -- 'cron' | 'webhook'
  messages_count  int,
  hard_gate_pct   numeric(5,2),
  posted          boolean,
  failed_reason   text
);
```

### 10.5 Manual re-verification (any time)

```bash
docker exec -it n8n-postgres-1 psql -U n8n -d n8n_pgvector -c "\dx"
docker exec -it n8n-postgres-1 psql -U n8n -d n8n_pgvector -c "\dt"
docker exec -it n8n-postgres-1 psql -U n8n -d n8n_pgvector \
  -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
```

## 11. Credentials (n8n) — LOCKED v0.3

All credentials referenced from `workflow.json` by **name** (never token). IDs included for cross-reference only — never embed tokens.

| Purpose | n8n Credential Name | n8n Credential ID | Type | Status |
|---|---|---|---|---|
| Linear API | `Linear - Rolla (Bender v2 digest)` | `iSfZn3cLB3gqd9gM` | `linearApi` | ✅ confirmed (Sven, 2026-05-25) |
| Zendesk API | `Zendesk - rollacompany (Bender v2 digest)` | `VYVmkciKDc9RACBS` | `zendeskApi` | ✅ confirmed (Sven, 2026-05-25) |
| Google Drive | `Google SA - n8n-whatsapp-uploader` | `m4byl5nfbNY3sE95` | `googleApi` | ✅ confirmed (Sven, exists in v1 workflow) |
| GitHub | existing PAT credential | (existing) | Header Auth (PAT, read repo) | confirm name during workflow build |
| Ollama (rolla-3080) | existing HTTP credential | (existing) | HTTP / none | likely already present from v1 |
| Slack | existing Slack credential | (existing) | Slack OAuth | confirmed — posts go to `#whatsapp-beta-feed` (`C0ANXU0NKFB`); `@whatsapp_bot` has post access; `[v2 SHADOW]` prefix in text |
| Postgres (v2 dedicated) | `Postgres - n8n-pgvector (Bender v2 digest)` | (pending UI wiring 2026-05-26) | Postgres | container live (`n8n-postgres-1`); password in `~/.secrets/n8n-pgvector.env` on n8n host |

**Rule:** workflow.json references credentials by `id` (and `name` for human reading). Tokens never appear in JSON, never in Slack, never in this repo. If a token is ever rotated, n8n keeps the same id/name — workflow continues to work.

## 12. Languages

- bge-m3 is natively multilingual (100+ languages, evaluated strong on slavic languages)
- qwen2.5vl is multilingual on screen-content (UI labels in non-Latin scripts handled cleanly)
- Whisper auto-detects language; transcribes natively
- No translation layer — LLM digest will preserve original quotes; English summary frames the section headings, body may include native quotes

## 13. Timeline

| Date | Owner | Deliverable |
|---|---|---|
| Sat-Sun May 23-24 | Sven | Review this doc; pull `qwen2.5vl:7b` on rolla-3080; provide Linear PAT + Zendesk token via n8n; confirm/correct architecture |
| Mon May 25 | Bender | Build VLM image handler + ffmpeg video pipeline + Whisper audio handler. Workflow JSON skeleton. |
| Tue May 26 | Sven | Run pgvector setup commands (§10) before 10:00. |
| Tue May 26 | Bender | Index ETL workflows (Linear nightly, codebase Sunday, Zendesk nightly); migration SQL. |
| Wed May 27 | Bender | Cross-reference + Zendesk dedupe + hallucination hard gate + assembly LLM + Slack post. *Shadow goes live evening of May 27.* |
| Thu May 28 → Wed June 3 | Both | Shadow mode runs daily. Sven personal review days 1-3. Bender tunes prompts/thresholds. |
| Fri June 5 | Sven | Cutover decision against §9 criteria. |
| Mon June 8 | Sven | Live cutover (if criteria hold). |

## 14. Open Items

**All v0.1 blockers resolved as of v0.2 (2026-05-25):**

1. ~~**Roadmap URL**~~ — RESOLVED. Linear projects are the roadmap. See §5.2 + §5.4.
2. ~~**Linear PAT**~~ — RESOLVED. Credential `Linear - Rolla (Bender v2 digest)` (id `iSfZn3cLB3gqd9gM`) exists in n8n.
3. ~~**Zendesk token**~~ — RESOLVED. Credential `Zendesk - rollacompany (Bender v2 digest)` (id `VYVmkciKDc9RACBS`) exists in n8n.

**Remaining v0.2 follow-ups (non-blocking, parallel to dev):**

- Drive folder ID for `members/borec-sven/whatsapp-beta/` — Sven to paste back into `whatsapp-beta/README.md` once folder syncs to Drive
- GitHub PAT credential name in n8n — confirm during workflow JSON build (Bender will ask if not already present)
- `#bender-requests` 2-line announcement post — draft delivered in DM (2026-05-25); Sven to paste at his discretion (non-gating)

## 15. Out of Scope for v1 (v1.1+ candidates)

- Remote VLM A/B (Claude/GPT-4o) — week 2 evaluation
- Backend repo scope widening — depends on v1 results
- Per-tester profile (recurring complainer detection, satisfaction trend)
- Auto-create Linear tickets for net-new substantive reports
- Sentiment trend dashboards
- Daily digest in BS/HR (current v1 is EN-only at section level)

## 16. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| qwen2.5vl:7b accuracy on tiny UI text | Week-2 A/B against Claude vision; escalate if needed |
| pgvector ivfflat recall on small dataset | `lists` tuned per table size; revisit at 10k+ rows |
| LLM hallucinates references despite prompt | Hard gate catches at validation — drops or fails |
| Backend bugs unaddressable | Explicit `needs backend reviewer` flag — not hidden |
| n8n Postgres growing unbounded | Add retention policy in v1.1 (90d for digests, indef for indices) |
| rolla-3080 OOM under concurrent video batch | Sequential frame processing; `OLLAMA_KEEP_ALIVE=24h` (Linear OPE-379, state `Done` 2026-05-25 — verified 2026-05-26) |

## 17. Out of n8n: required scripts

**Repo location — LOCKED v0.3:** `bender-boop/wa-digest-v2` (branch `main`, public). Used as the source-of-truth until/unless work transfers into `Rolla-Health-Fitness/`. Drive mirror lives at `members/borec-sven/outputs/bender whatsapp digest/v2-design/`.

| Path | Purpose | Status |
|---|---|---|
| `scripts/validate_digest.py` | Hard gate regex + verification (§6.2-6.3) | **DELIVERED 2026-05-26** |
| `scripts/index_linear.py` | Linear API → embeddings → BOTH `linear_index` (issues) AND `linear_projects_index` (roadmap) in one nightly run | pending |
| `scripts/index_codebase.py` | GitHub clone → file walk → embeddings → codebase_index | pending |
| `scripts/index_zendesk.py` | Zendesk API → embeddings → zendesk_index | pending |
| `scripts/extract_keyframes.sh` | ffmpeg wrapper (callable from n8n exec node) | pending |

n8n workflow JSON lives at the repo root (`workflow.json`), not under `workflows/` — see §3 trigger block and v0.3 changelog.

---

*End of v0.3 — Sven 6-point review applied 2026-05-26.*
