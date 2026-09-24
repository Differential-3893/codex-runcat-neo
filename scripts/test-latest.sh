#!/bin/sh
set -eu

HOOK="${CODEX_HOME:-$HOME/.codex}/runcat-neo-hook.py"
OUT="${RUNCAT_OUT_FILE:-${CODEX_HOME:-$HOME/.codex}/runcat-usage.json}"

if [ ! -x "$HOOK" ]; then
  echo "Hook is not installed or executable: $HOOK" >&2
  exit 1
fi

transcript="$(
  find "${CODEX_HOME:-$HOME/.codex}/sessions" -name '*.jsonl' -type f -print0 \
  | xargs -0 stat -f '%m %N' \
  | sort -nr \
  | head -n 1 \
  | cut -d ' ' -f 2-
)"

if [ -z "$transcript" ]; then
  echo "No Codex transcript found." >&2
  exit 1
fi

printf '{"transcript_path":"%s"}\n' "$transcript" | "$HOOK"
python3 -m json.tool "$OUT"
