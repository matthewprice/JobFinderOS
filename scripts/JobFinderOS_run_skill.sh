#!/usr/bin/env bash
# Run a Claude Code skill locally (subscription auth). Writes to local vault/ only.
# Usage: JobFinderOS_run_skill.sh <log-label> <skill-name>
# Example: JobFinderOS_run_skill.sh jobs-daily jobs-daily
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/logs"

LABEL="${1:?log label required (e.g. jobs-daily)}"
SKILL="${2:?skill name required (e.g. jobs-daily)}"
CMD_FILE="$ROOT/.claude/commands/${SKILL}.md"
if [[ ! -f "$CMD_FILE" ]]; then
  echo "JobFinderOS_run_skill: missing skill file: $CMD_FILE" >&2
  exit 2
fi

CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || true)}"
if [[ -z "$CLAUDE_BIN" || ! -x "$CLAUDE_BIN" ]]; then
  echo "JobFinderOS_run_skill: claude CLI not found (set CLAUDE_BIN)" >&2
  exit 127
fi

TIMEOUT_SEC="${JOBFINDEROS_SKILL_TIMEOUT_SEC:-1800}"
ALLOWED_TOOLS="${JOBFINDEROS_SKILL_ALLOWED_TOOLS:-Agent,Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch}"

export PATH="${HOME}/.local/bin:${PATH}"

"$ROOT/scripts/JobFinderOS_log_run.sh" "$LABEL" start
# Explicit tools + a permission mode instead of --dangerously-skip-permissions (a scheduled
# run reads untrusted pages and email unattended). JSON output so tokens and cost get logged.
PERMISSION_MODE="${JOBFINDEROS_PERMISSION_MODE:-acceptEdits}"
mkdir -p "$ROOT/logs/skills"
OUT_JSON="$ROOT/logs/skills/$(date '+%Y%m%dT%H%M%S')-${LABEL}.json"
set +e
if command -v timeout >/dev/null 2>&1; then
  timeout "$TIMEOUT_SEC" "$CLAUDE_BIN" -p "/${SKILL}" \
    --output-format json \
    --allowedTools "$ALLOWED_TOOLS" \
    --permission-mode "$PERMISSION_MODE" --permission-prompts none --no-session-persistence \
    < /dev/null > "$OUT_JSON"
  ec=$?
  if [[ "$ec" -eq 124 ]]; then
    "$ROOT/scripts/JobFinderOS_log_run.sh" "$LABEL" "failed (timeout ${TIMEOUT_SEC}s)"
    exit "$ec"
  fi
else
  "$CLAUDE_BIN" -p "/${SKILL}" \
    --output-format json \
    --allowedTools "$ALLOWED_TOOLS" \
    --permission-mode "$PERMISSION_MODE" --permission-prompts none --no-session-persistence \
    < /dev/null > "$OUT_JSON"
  ec=$?
fi
set -e

PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3
USAGE="$("$PY" - "$OUT_JSON" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("no usage data"); raise SystemExit(0)
u = d.get("usage") or {}
tin = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
print(f"est. ${float(d.get('total_cost_usd') or 0):.2f}, {tin} in / {int(u.get('output_tokens') or 0)} out tokens, {int(d.get('num_turns') or 0)} turns"
      + (f", ERROR {d.get('terminal_reason')}" if d.get("is_error") else ""))
PYEOF
)"
if grep -q '"is_error": *true' "$OUT_JSON" 2>/dev/null; then ec=1; fi

if [[ "$ec" -eq 0 ]]; then
  "$ROOT/scripts/JobFinderOS_log_run.sh" "$LABEL" "completed (${USAGE})"
  # Repair any literal-backslash vault paths a skill run may have written
  "$PY" "$ROOT/scripts/jobfinderos_fix_backslash_paths.py" || true
else
  "$ROOT/scripts/JobFinderOS_log_run.sh" "$LABEL" "failed (exit $ec)"
  exit "$ec"
fi
