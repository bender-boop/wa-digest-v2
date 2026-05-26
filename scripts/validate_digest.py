#!/usr/bin/env python3
"""
validate_digest.py — Hard gate validator for WhatsApp Beta Digest v2.

Implements architecture §6.2 (per-reference validation) and §6.3
(whole-digest threshold). The LLM at Stage 3 drafts a ≤300-word digest;
this script verifies every reference it makes — Linear IDs, file paths,
Zendesk ticket numbers — against the `verified_matches` block produced
by Stage 2 (pgvector queries + Zendesk lookups). Anything unverified is
either annotated inline or has its sentence dropped. If the unverified
fraction crosses --drop-pct-threshold (default 0.20), the whole digest
fails the gate and Stage 3 routes to the FAIL output path.

Invocation modes:

  (1) CLI args  (current workflow.json wiring, n8n executeCommand):
        python3 validate_digest.py \
          --digest "<digest text>" \
          --matches '<verified_matches JSON>' \
          --drop-pct-threshold 0.20 \
          [--mode annotate|drop]

  (2) stdin     (preferred for large multiline digests — sidesteps
                shell-quoting fragility):
        echo '{"digest":"...","matches":{...},"drop_pct_threshold":0.20}' \
          | python3 validate_digest.py --stdin

Output (stdout, single-line JSON):
  {
    "passed":              bool,
    "dropped_pct":         float,   # rounded to 4 dp
    "total_references":    int,
    "verified_references": int,
    "dropped_references":  [{"type":"linear|path|zendesk","value":"...","sentence":"..."}],
    "cleaned_digest":      str
  }

Exit codes:
  0 — script ran successfully (check `passed` in JSON for gate outcome)
  2 — malformed input / argument error

Stdlib-only — no pip dependencies. Tested on python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Set


# ---------------------------------------------------------------------------
# Reference patterns
# ---------------------------------------------------------------------------

# Linear IDs: 2–5 uppercase letters, hyphen, 1+ digits.
# Matches OPE-379, PE-123, BOR-456, MAR-12, MS-7.
# Negative lookahead excludes 'ZD-' (Zendesk shorthand handled separately).
LINEAR_ID_RE = re.compile(r"\b(?!ZD-)([A-Z]{2,5}-\d+)\b")

# File paths: one or more directory components followed by a filename.
# Requires at least one '/'. Excludes URLs (negative lookbehind for "://").
# Allows alphanumerics, underscore, dot, dash inside components.
FILE_PATH_RE = re.compile(
    r"(?<!://)(?<!:/)\b((?:[A-Za-z0-9_.-]+/){1,}[A-Za-z0-9_.-]+)\b"
)

# Zendesk ticket numbers: explicit "#1234", "ticket #1234", "ZD-1234".
# 3+ digits to avoid catching version numbers like "v12".
ZENDESK_RE = re.compile(
    r"(?:#|ticket\s+#?|ZD-)(\d{3,})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Verified-set extraction
# ---------------------------------------------------------------------------

def build_verified_sets(matches: Dict[str, Any]) -> Dict[str, Set[str]]:
    """
    Build lookup sets from the Stage-2 verified_matches block.

    Expected shape (any subset may be absent / empty):
      {
        "linear":        [{"issue_id": "OPE-379", "title": "...", ...}, ...],
        "roadmap":       [{"project_id": "abc", "name": "WA Digest v2"}, ...],
        "codebase":      [{"repo": "...", "path": "src/foo.ts", ...}, ...],
        "zendesk_hard":  [{"ticket_id": 12345, ...}, ...],
        "zendesk_soft":  [{"ticket_id": 67890, ...}, ...]
      }
    """
    linear: Set[str] = set()
    for m in matches.get("linear") or []:
        if isinstance(m, dict) and "issue_id" in m:
            linear.add(str(m["issue_id"]).upper())

    roadmap_ids: Set[str] = set()
    roadmap_names: Set[str] = set()
    for m in matches.get("roadmap") or []:
        if isinstance(m, dict):
            if "project_id" in m:
                roadmap_ids.add(str(m["project_id"]))
            if "name" in m:
                roadmap_names.add(str(m["name"]))

    paths: Set[str] = set()
    for m in matches.get("codebase") or []:
        if isinstance(m, dict) and "path" in m:
            paths.add(str(m["path"]))

    zd: Set[str] = set()
    for bucket in ("zendesk_hard", "zendesk_soft"):
        for m in matches.get(bucket) or []:
            if isinstance(m, dict) and "ticket_id" in m:
                zd.add(str(m["ticket_id"]))

    return {
        "linear": linear,
        "path": paths,
        "zendesk": zd,
        "roadmap_id": roadmap_ids,
        "roadmap_name": roadmap_names,
    }


# ---------------------------------------------------------------------------
# Sentence-level scanning
# ---------------------------------------------------------------------------

def split_sentences(text: str) -> List[str]:
    """
    Split into sentence-like chunks while preserving paragraph structure.

    We keep blank/whitespace-only lines as-is so the cleaned digest
    retains its section breaks. Within each non-empty line we split on
    sentence-terminating punctuation followed by whitespace.
    """
    out: List[str] = []
    for line in text.splitlines(keepends=True):
        if not line.strip():
            out.append(line)
            continue
        body = line.rstrip("\n")
        had_newline = line.endswith("\n")
        chunks = re.split(r"(?<=[.!?])\s+", body)
        for i, chunk in enumerate(chunks):
            if not chunk:
                continue
            if i == len(chunks) - 1:
                out.append(chunk + ("\n" if had_newline else ""))
            else:
                out.append(chunk + " ")
    return out


def scan_references(sentence: str) -> List[Dict[str, str]]:
    """Return every reference candidate found in a sentence."""
    refs: List[Dict[str, str]] = []
    for m in LINEAR_ID_RE.finditer(sentence):
        refs.append({"type": "linear", "value": m.group(1)})
    for m in FILE_PATH_RE.finditer(sentence):
        val = m.group(1)
        # Defensive filters: URLs slip through if lookbehinds miss; require
        # at least one slash (the regex enforces that, kept here for clarity).
        if val.startswith("http"):
            continue
        if "/" not in val:
            continue
        refs.append({"type": "path", "value": val})
    for m in ZENDESK_RE.finditer(sentence):
        refs.append({"type": "zendesk", "value": m.group(1)})
    return refs


# ---------------------------------------------------------------------------
# Validation core
# ---------------------------------------------------------------------------

def validate(
    digest: str,
    matches: Dict[str, Any],
    threshold: float,
    mode: str,
) -> Dict[str, Any]:
    verified = build_verified_sets(matches)
    sentences = split_sentences(digest)

    total = 0
    verified_count = 0
    dropped: List[Dict[str, str]] = []
    cleaned: List[str] = []

    for sent in sentences:
        refs = scan_references(sent)
        if not refs:
            cleaned.append(sent)
            continue

        sentence_clean = True
        sent_dropped: List[Dict[str, str]] = []
        for ref in refs:
            total += 1
            rtype = ref["type"]
            value = ref["value"]
            lookup = value.upper() if rtype == "linear" else value
            if lookup in verified.get(rtype, set()):
                verified_count += 1
            else:
                sentence_clean = False
                sent_dropped.append({
                    "type": rtype,
                    "value": value,
                    "sentence": sent.strip(),
                })

        if sentence_clean:
            cleaned.append(sent)
        else:
            dropped.extend(sent_dropped)
            if mode == "annotate":
                annotated = sent
                for d in sent_dropped:
                    annotated = annotated.replace(
                        d["value"],
                        f"{d['value']} (unverified)",
                        1,
                    )
                cleaned.append(annotated)
            elif sent.endswith("\n"):
                # mode == "drop": omit the sentence but preserve line break
                # so paragraph structure survives.
                cleaned.append("\n")
            # else: drop silently (mid-paragraph sentence)

    dropped_pct = (total - verified_count) / total if total else 0.0
    # Per architecture §6.3: fail when dropped_pct > threshold (strict).
    # So at threshold exactly, the digest still passes.
    passed = dropped_pct <= threshold

    return {
        "passed": passed,
        "dropped_pct": round(dropped_pct, 4),
        "total_references": total,
        "verified_references": verified_count,
        "dropped_references": dropped,
        "cleaned_digest": "".join(cleaned).strip(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _emit_error(message: str) -> None:
    """Emit a single-line JSON error and (caller) exit 2."""
    print(json.dumps({"passed": False, "error": message}))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hard-gate digest validator (WhatsApp Beta Digest v2)",
    )
    parser.add_argument("--digest", help="Digest text (CLI mode)")
    parser.add_argument(
        "--matches",
        help="verified_matches JSON string (CLI mode)",
    )
    parser.add_argument(
        "--drop-pct-threshold",
        type=float,
        default=0.20,
        help="Fail if (unverified / total) >= this. Default 0.20",
    )
    parser.add_argument(
        "--mode",
        choices=["annotate", "drop"],
        default="annotate",
        help="On unverified ref: annotate inline (default) or drop sentence",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read {digest, matches, drop_pct_threshold?, mode?} JSON from stdin",
    )
    args = parser.parse_args()

    if args.stdin:
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            _emit_error(f"stdin JSON parse error: {exc}")
            return 2
        if not isinstance(payload, dict):
            _emit_error("stdin payload must be a JSON object")
            return 2
        digest = payload.get("digest", "")
        matches = payload.get("matches") or {}
        threshold = float(
            payload.get("drop_pct_threshold", args.drop_pct_threshold)
        )
        mode = payload.get("mode", args.mode)
    else:
        if args.digest is None or args.matches is None:
            _emit_error(
                "--digest and --matches are required when not using --stdin"
            )
            return 2
        digest = args.digest
        try:
            matches = json.loads(args.matches) if args.matches else {}
        except json.JSONDecodeError as exc:
            _emit_error(f"--matches JSON parse error: {exc}")
            return 2
        threshold = args.drop_pct_threshold
        mode = args.mode

    if not isinstance(matches, dict):
        _emit_error("matches must decode to a JSON object")
        return 2
    if not 0.0 <= threshold <= 1.0:
        _emit_error("drop-pct-threshold must be in [0, 1]")
        return 2
    if mode not in ("annotate", "drop"):
        _emit_error("mode must be 'annotate' or 'drop'")
        return 2

    result = validate(digest, matches, threshold, mode)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
