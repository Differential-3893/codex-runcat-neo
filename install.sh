#!/bin/sh
set -eu

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
HOOK_SRC="$SCRIPT_DIR/runcat-neo-hook.py"
HOOK_DST="$CODEX_HOME/runcat-neo-hook.py"
HOOKS_JSON="$CODEX_HOME/hooks.json"
STAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p "$CODEX_HOME"

if [ -f "$HOOK_DST" ]; then
  cp -p "$HOOK_DST" "$HOOK_DST.backup-$STAMP"
  echo "Backed up existing hook to:"
  echo "  $HOOK_DST.backup-$STAMP"
fi

if [ -f "$HOOKS_JSON" ]; then
  cp -p "$HOOKS_JSON" "$HOOKS_JSON.backup-$STAMP"
  echo "Backed up hooks.json to:"
  echo "  $HOOKS_JSON.backup-$STAMP"
fi

cp "$HOOK_SRC" "$HOOK_DST"
chmod 755 "$HOOK_DST"

HOOK_DST="$HOOK_DST" HOOKS_JSON="$HOOKS_JSON" python3 - <<'PY'
import json
import os
from pathlib import Path

hook_path = os.environ["HOOK_DST"]
hooks_path = Path(os.environ["HOOKS_JSON"])

if hooks_path.exists():
    try:
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Refusing to overwrite invalid JSON in {hooks_path}: {exc}")
else:
    data = {}

if not isinstance(data, dict):
    raise SystemExit(f"Refusing to overwrite non-object JSON in {hooks_path}")

hooks = data.setdefault("hooks", {})
if not isinstance(hooks, dict):
    raise SystemExit(f"{hooks_path}: top-level 'hooks' must be an object")

stop = hooks.setdefault("Stop", [])
if not isinstance(stop, list):
    raise SystemExit(f"{hooks_path}: hooks.Stop must be an array")

already_present = False

for group in stop:
    if not isinstance(group, dict):
        continue
    handlers = group.get("hooks")
    if not isinstance(handlers, list):
        continue
    for handler in handlers:
        if not isinstance(handler, dict):
            continue
        if handler.get("type") == "command" and handler.get("command") == hook_path:
            handler["timeout"] = 5
            already_present = True

if not already_present:
    stop.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": hook_path,
                    "timeout": 5,
                }
            ]
        }
    )

hooks_path.write_text(
    json.dumps(data, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
PY

echo
echo "Installed Codex → RunCat Neo hook:"
echo "  $HOOK_DST"
echo
echo "Registered Stop hook in:"
echo "  $HOOKS_JSON"
echo
echo "Next:"
echo "  1. Launch Codex CLI and review/trust the hook if prompted."
echo "  2. Complete one Codex turn."
echo "  3. Add ~/.codex/runcat-usage.json to RunCat Neo Custom Metrics."
echo
echo "Optional manual test:"
echo "  ./scripts/test-latest.sh"
