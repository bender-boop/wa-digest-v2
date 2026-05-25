#!/usr/bin/env bash
# setup-pgvector.sh
# WhatsApp Beta Digest v2 — pgvector + schema bootstrap
# Target: n8n Postgres container (default name: n8n-postgres-1, override with $PG_CONTAINER)
# Run on: n8n host (10.10.103.200), Tuesday May 26 2026 morning, before 10:00 CEST
# Owner: Sven Borec (operator) — Bender (designer)
#
# This script is IDEMPOTENT — safe to re-run. Uses CREATE EXTENSION IF NOT EXISTS,
# CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS throughout.

set -euo pipefail

# ─── Config (override via env) ────────────────────────────────────────────────
PG_CONTAINER="${PG_CONTAINER:-n8n-postgres-1}"
PG_USER="${PG_USER:-n8n}"
PG_DB="${PG_DB:-n8n}"
PG_IMAGE_HAS_PGVECTOR="${PG_IMAGE_HAS_PGVECTOR:-auto}"   # auto | yes | no

# ─── Helpers ──────────────────────────────────────────────────────────────────
log()  { printf "\033[1;34m[setup-pgvector]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[setup-pgvector WARN]\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m[setup-pgvector FAIL]\033[0m %s\n" "$*" >&2; exit 1; }

require() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

# ─── Pre-flight ───────────────────────────────────────────────────────────────
require docker

if ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
  warn "Container '$PG_CONTAINER' not running. Available Postgres containers:"
  docker ps --filter "ancestor=postgres" --format "  - {{.Names}} ({{.Image}})" || true
  docker ps --filter "name=postgres" --format "  - {{.Names}} ({{.Image}})" || true
  fail "Set PG_CONTAINER=<name> and re-run."
fi

log "Using container: $PG_CONTAINER  (user=$PG_USER  db=$PG_DB)"

# ─── Step 1 — Install pgvector if the image doesn't already ship it ───────────
log "Step 1/4 — Detecting pgvector availability inside container..."

if [[ "$PG_IMAGE_HAS_PGVECTOR" == "auto" ]]; then
  if docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
       "SELECT 1 FROM pg_available_extensions WHERE name='vector';" 2>/dev/null | grep -q 1; then
    log "  ✓ pgvector extension is available in this image — skipping apt install."
    PG_IMAGE_HAS_PGVECTOR="yes"
  else
    log "  ✗ pgvector NOT in image — will attempt apt install."
    PG_IMAGE_HAS_PGVECTOR="no"
  fi
fi

if [[ "$PG_IMAGE_HAS_PGVECTOR" == "no" ]]; then
  log "  Installing postgresql-<major>-pgvector via apt inside container..."
  PG_MAJOR=$(docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc "SHOW server_version_num;" | awk '{print int($1/10000)}')
  log "  Detected Postgres major version: $PG_MAJOR"
  docker exec -i "$PG_CONTAINER" bash -lc "apt-get update && apt-get install -y postgresql-${PG_MAJOR}-pgvector" \
    || fail "apt install failed. If your image is alpine-based, switch to a pgvector-enabled image (e.g. pgvector/pgvector:pg${PG_MAJOR}) instead."

  log "  Restarting container to load extension..."
  docker restart "$PG_CONTAINER" >/dev/null

  log "  Waiting for Postgres to accept connections..."
  for i in {1..30}; do
    if docker exec -i "$PG_CONTAINER" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
      log "  ✓ Postgres ready after ${i}s"
      break
    fi
    sleep 1
    if [[ $i -eq 30 ]]; then fail "Postgres did not come back online within 30s"; fi
  done
fi

# ─── Step 2 — CREATE EXTENSION ────────────────────────────────────────────────
log "Step 2/4 — Creating vector extension in database '$PG_DB'..."
docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector;"
log "  ✓ vector extension active"

# ─── Step 3 — Apply schema (v0.2: includes linear_projects_index) ─────────────
log "Step 3/4 — Applying wa-digest-v2 schema (idempotent)..."

docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 <<'SQL'
-- WhatsApp Beta Digest v2 — schema v0.2
-- Source of truth: architecture.md §10 (in this repo)

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid() in digest_runs

-- ── linear_index (issues) ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS linear_index (
  issue_id        text PRIMARY KEY,
  title           text NOT NULL,
  description     text,
  state           text,
  priority        int,
  labels          text[],
  project_id      text,
  updated_at      timestamptz,
  embedding       vector(1024)
);
CREATE INDEX IF NOT EXISTS linear_index_embedding_idx
  ON linear_index USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- ── linear_projects_index (roadmap layer — v0.2 replaces standalone roadmap_index) ──
CREATE TABLE IF NOT EXISTS linear_projects_index (
  project_id      text PRIMARY KEY,
  name            text NOT NULL,
  description     text,
  state           text,
  lead_email      text,
  target_date     date,
  teams           text[],
  updated_at      timestamptz,
  embedding       vector(1024)
);
CREATE INDEX IF NOT EXISTS linear_projects_index_embedding_idx
  ON linear_projects_index USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 50);

-- ── codebase_index ───────────────────────────────────────────────────────────
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

-- ── zendesk_index ────────────────────────────────────────────────────────────
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

-- ── Audit / observability ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS digest_runs (
  run_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_at          timestamptz NOT NULL DEFAULT now(),
  trigger         text NOT NULL,                    -- 'cron' | 'webhook'
  messages_count  int,
  hard_gate_pct   numeric(5,2),
  posted          boolean,
  failed_reason   text
);
CREATE INDEX IF NOT EXISTS digest_runs_run_at_idx ON digest_runs (run_at DESC);

-- ── v0.2 migration cleanup ───────────────────────────────────────────────────
-- If a previous run created the standalone roadmap_index from v0.1 spec, drop it.
DROP TABLE IF EXISTS roadmap_index CASCADE;

SQL

log "  ✓ Schema applied"

# ─── Step 4 — Verify ──────────────────────────────────────────────────────────
log "Step 4/4 — Verifying installation..."

echo
echo "  Extensions:"
docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -c "\dx vector"

echo
echo "  Tables (wa-digest-v2):"
docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -c "\dt linear_index linear_projects_index codebase_index zendesk_index digest_runs"

echo
echo "  Index check:"
docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc "
SELECT tablename, indexname
FROM pg_indexes
WHERE indexname LIKE '%_embedding_idx'
ORDER BY tablename;
"

echo
VECTOR_OK=$(docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc "SELECT count(*) FROM pg_extension WHERE extname = 'vector';")
if [[ "$VECTOR_OK" == "1" ]]; then
  log "✅ DONE — pgvector + 5 tables + 5 ivfflat indices in place. Ready for ETL workflows."
else
  fail "vector extension not detected after setup — investigate."
fi

echo
log "Next step (Bender will deliver Tue): scripts/index_linear.py — populates linear_index + linear_projects_index."
