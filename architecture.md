---
title: WhatsApp Beta Digest v2 — Architecture
status: approved-for-implementation
version: 0.2
author: Bender (designer/maintainer)
operator: Sven Borec (runtime owner)
created: 2026-05-22
last-updated: 2026-05-25
review-by: 2026-05-25 (Mon morning) — COMPLETE, all 3 unblockers resolved
target-shadow-live: 2026-05-27 (Wed)
target-cutover-decision: 2026-06-05 (Fri)
target-live-cutover: 2026-06-08 (Mon)
changelog:
  - "v0.2 (2026-05-25) — All §14 unblockers resolved. Roadmap = Linear projects (no separate Doc). Credential names + IDs locked in. Status flipped to approved-for-implementation."
  - "v0.1 (2026-05-22) — Initial draft for Sven review."
---

## 1. Goal

Daily digest that *interprets* WhatsApp beta tester reports (text + screenshots + videos + voice notes), *cross-checks* them against Linear issues, the Rolla codebase, the roadmap, and Zendesk tickets, and outputs a ≤300-word top-level Slack post + long-form thread reply — with *zero invented references*.

v1 of the digest (`kj0v8HDESttbVI2t`) is text-only and treats `[PHOTO …]` markers as strings. v2 *reads* the media and grounds every claim in a verifiable source.

## 2. Posture

- Parallel n8n workflow — *not* a modification of `kj0v8HDESttbVI2t`. v1 keeps running untouched during shadow.
- Shadow mode posts to `#wa-digest-v2-shadow` (private, Sven-only first 3 days).
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
│  LLM (gemma2:9b or llama3.1:8b) → drafts ≤300-word digest           │
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
│  • Slack top-level → #wa-digest-v2-shadow (≤300 words)              │
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
- Output: `observed_content` string attached to the message object
- Timeout: 30s per image; on timeout, fall back to `[VLM timeout — see raw image at <path>]`

### 4.2 Video handler

- Tooling: ffmpeg
- Keyframe extraction: `ffmpeg -i video_<sender>_<ts>.mp4 -vf "fps=0.5,scale=1280:-2" -frames:v 10 -q:v 4 frame_%02d.jpg`
- Dedupe near-identical frames pre-VLM: pHash distance threshold 8 (perceptual hash)
- Batch through `qwen2.5vl:7b` — describe the sequence as a flow
- Skip audio track entirely for v1 (most beta video is silent screen capture)

### 4.3 Audio handler

- Tooling: `whisper.cpp` medium model on rolla-3080
- Output folded into message `text` field with prefix `[AUDIO transcript: …]`
- Language auto-detect; BS/HR/SL transcribed natively (no translation)

## 5. Stage 2 — Cross-Reference

### 5.1 Embedding model

- `bge-m3` (1024d), multilingual native — handles BS/HR/SL/EN without preprocessing

### 5.2 Linear index (issues + projects)

**Confirmed v0.2:** Linear is the single source of truth for both _issues_ (tactical) AND _projects_ (roadmap layer). `index_linear.py` writes to two tables in the same nightly run.

- Refresh: nightly 02:00 via Linear API GraphQL
- n8n credential reference: `Linear - Rolla (Bender v2 digest)` (id `iSfZn3cLB3gqd9gM`, type `linearApi`)
- Issues query: all non-cancelled issues across all teams visible to the PAT
- Projects query: all active + planned projects (cancelled/completed excluded)
- Stage 2 emits TWO match arrays in `verified_matches`: `linear[]` (issues) and `roadmap[]` (projects). Stage 3 hard gate validates both identically.

Schema (issues):
```sql
CREATE TABLE linear_index (
  issue_id text PRIMARY KEY, title text NOT NULL, description text,
  state text, priority int, labels text[], project_id text,
  updated_at timestamptz, embedding vector(1024)
);
```

Schema (projects = roadmap):
```sql
CREATE TABLE linear_projects_index (
  project_id text PRIMARY KEY, name text NOT NULL, description text,
  state text, lead_email text, target_date date, teams text[],
  updated_at timestamptz, embedding vector(1024)
);
```

### 5.3 Codebase index

- Refresh: Sunday 03:00 via GitHub API + local clone
- Scope: 9 repos accessible via Bender's GitHub PAT (`docs`, `wl-rolla-mobile`, `rolla-sdk`, `rolla-sdk-release-{android,ios,react-native,test-android,test-ios}`, `rolla-sdk-documentation`)
- Per-file extraction: path, first 30 lines (docstring), function/class signatures, last-commit SHA + message + date
- Backend bugs: if a report's top match would require a repo Bender can't see, emit `→ matches: needs backend reviewer`

### 5.4 Roadmap source — RESOLVED v0.2

**Decision (Sven, 2026-05-25):** No separate roadmap document exists. Linear projects are the roadmap layer.

- Implementation lives entirely in §5.2 (`linear_projects_index`)
- Stage 3 hard gate validates roadmap mentions against `linear_projects_index.name` matches surfaced in Stage 2
- The previously-spec'd standalone `roadmap_index` table from v0.1 is **removed** — do not create it during migration
- v1.1 candidate: if/when a richer roadmap doc emerges, layer it ALONGSIDE the projects index, do not replace

### 5.5 Zendesk dedupe

- **Stage 1 — hard match**: Zendesk Search API for tickets from same tester (phone first, email fallback) within ±48h → silent merge
- **Stage 2 — soft match**: bge-m3 cosine ≥0.82 within ±7d window, any sender → surface for human review

## 6. Stage 3 — Assembly + Hallucination Hard Gate

### 6.1 LLM call

- Model: `gemma2:9b` first attempt; fallback `llama3.1:8b`
- Prompt enforces: ≤300 words, only references from `verified_matches`, "(unverified)" when uncertain, BS/HR/SL/EN equal-status

### 6.2 Hard gate — per-reference validation

| Pattern | Source of truth | Action on fail |
|---|---|---|
| `[A-Z]+-\d+` (e.g. OPE-229) | `verified_matches.linear[].issue_id` | drop the entire reference line |
| File path with `.` extension or directory `/` | `verified_matches.codebase[].path` | drop the entire reference line |
| Roadmap item names (Linear project names) | `verified_matches.roadmap[].name` (sourced from `linear_projects_index`) | drop the entire reference line |

### 6.3 Hard gate — whole-digest threshold

- If `dropped / total > 0.20` → **do not post the LLM output**
- Instead post stub: "⚠️ Digest YYYY-MM-DD failed validation (X% references unverified). Full debug payload in thread."
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

## 9. Cutover Criteria (per Sven, all hold 5 consecutive weekdays)

1. Digest fires at 08:30 *and* on every webhook trigger — no missed runs
2. Every screenshot/video in `messages_<date>.json` is *looked at* by the VLM, with content surfacing in the digest when relevant
3. Codebase + roadmap cross-check line appears for ≥80% of substantive reports
4. **Zero** hallucinated Linear IDs, file paths, or roadmap items. If unsure, says unsure
5. BS/HR/SL messages handled identically to English
6. Digest stays ≤300 words at top level. Long detail in thread, not the top post

## 10. pgvector Setup

See `setup-pgvector.sh` in this repo — idempotent bootstrap script. Creates `vector` + `pgcrypto` extensions, 5 tables (linear_index, linear_projects_index, codebase_index, zendesk_index, digest_runs) and 5 ivfflat indices. Drops legacy `roadmap_index` if v0.1 migration ran.

Run on n8n host Tue May 26 morning before 10:00 CEST:
```bash
bash setup-pgvector.sh
# or override container name:
PG_CONTAINER=my-postgres bash setup-pgvector.sh
```

## 11. Credentials (n8n) — LOCKED v0.2

All credentials referenced from `workflow.json` by **name** (never token). IDs included for cross-reference only.

| Purpose | n8n Credential Name | n8n Credential ID | Type | Status |
|---|---|---|---|---|
| Linear API | `Linear - Rolla (Bender v2 digest)` | `iSfZn3cLB3gqd9gM` | `linearApi` | ✅ confirmed (Sven, 2026-05-25) |
| Zendesk API | `Zendesk - rollacompany (Bender v2 digest)` | `VYVmkciKDc9RACBS` | `zendeskApi` | ✅ confirmed (Sven, 2026-05-25) |
| Google Drive | `Google SA - n8n-whatsapp-uploader` | `m4byl5nfbNY3sE95` | `googleApi` | ✅ confirmed (Sven, exists in v1 workflow) |
| GitHub | existing PAT credential | (existing) | Header Auth (PAT, read repo) | confirm name during workflow build |
| Ollama (rolla-3080) | existing HTTP credential | (existing) | HTTP / none | likely already present from v1 |
| Slack | existing Slack credential | (existing) | Slack OAuth | confirmed — `#wa-digest-v2-shadow` exists, bot invited |
| Postgres | existing n8n Postgres | (existing) | Postgres | existing |

**Rule:** workflow.json references credentials by `id` (and `name` for human reading). Tokens never appear in JSON, never in Slack, never in this repo.

## 13. Timeline

| Date | Owner | Deliverable |
|---|---|---|
| Mon May 25 | Bender | v0.2 architecture + workflow skeleton + pgvector script (delivered via GitHub due to DriveFS daemon dead) |
| Tue May 26 | Sven | Run `setup-pgvector.sh` on n8n host before 10:00 |
| Tue May 26 | Bender | Index ETL workflows (Linear nightly, codebase Sunday, Zendesk nightly); migration SQL |
| Wed May 27 | Bender | Cross-reference + Zendesk dedupe + hallucination hard gate + assembly LLM + Slack post. **Shadow goes live evening of May 27.** |
| Thu May 28 → Wed June 3 | Both | Shadow mode runs daily. Sven personal review days 1-3. Bender tunes prompts/thresholds |
| Fri June 5 | Sven | Cutover decision against §9 criteria |
| Mon June 8 | Sven | Live cutover (if criteria hold) |

## 14. Open Items

**All v0.1 blockers resolved as of v0.2 (2026-05-25):**

1. ~~**Roadmap URL**~~ — RESOLVED. Linear projects are the roadmap. See §5.2 + §5.4.
2. ~~**Linear PAT**~~ — RESOLVED. Credential `Linear - Rolla (Bender v2 digest)` (id `iSfZn3cLB3gqd9gM`) exists in n8n.
3. ~~**Zendesk token**~~ — RESOLVED. Credential `Zendesk - rollacompany (Bender v2 digest)` (id `VYVmkciKDc9RACBS`) exists in n8n.

**New v0.2 blocker (infra):**

- **DriveFS daemon dead** on Bender machine for `bender@rolla.app` (pid 1240, not alive). Drive reads stale ≥6 days, writes don't propagate. `#bender-requests` ticket filed 2026-05-25. Target fix: Tue EOD so Wed shadow stays on schedule.

**Remaining v0.2 follow-ups (non-blocking, parallel to dev):**

- Drive folder ID for `members/borec-sven/whatsapp-beta/` — Sven to fill in `whatsapp-beta/README.md` once Drive is back
- GitHub PAT credential name in n8n — confirm during workflow JSON build

## 17. Out of n8n: required scripts

These will land in this repo (or be transferred to `Rolla-Health-Fitness/wa-digest-v2`) as Tue/Wed work progresses:

- `scripts/index_linear.py` — Linear API → embeddings → BOTH `linear_index` (issues) AND `linear_projects_index` (roadmap) in one nightly run
- `scripts/index_codebase.py` — GitHub clone → file walk → embeddings → codebase_index
- `scripts/index_zendesk.py` — Zendesk API → embeddings → zendesk_index
- `scripts/extract_keyframes.sh` — ffmpeg wrapper (callable from n8n exec node)
- `scripts/validate_digest.py` — hard gate regex + verification

---

*End of v0.2 — approved for implementation. All v0.1 unblockers resolved 2026-05-25.*
