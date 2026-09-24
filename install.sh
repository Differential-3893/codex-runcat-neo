#!/bin/sh
set -eu

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
HOOK_SRC="$SCRIPT_DIR/runcat-neo-hook.py"
HOOK_DST="$CODEX_HOME/runcat-neo-hook.py"
HOOKS_JSON="$CODEX_HOME/hooks.json"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
REFRESH_LABEL="dev.runcat.codex-usage"
REFRESH_PLIST="$LAUNCH_AGENTS/$REFRESH_LABEL.plist"
STAMP=$(date +%Y%m%d-%H%M%S)
PYTHON_BIN=$(command -v python3 || true)

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This integration requires macOS." >&2
  exit 1
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "python3 was not found on PATH." >&2
  exit 1
fi

mkdir -p "$CODEX_HOME" "$LAUNCH_AGENTS"

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

HOOK_DST="$HOOK_DST" HOOKS_JSON="$HOOKS_JSON" "$PYTHON_BIN" - <<'PY'
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

"$PYTHON_BIN" - "$REFRESH_PLIST" "$PYTHON_BIN" "$HOOK_DST" "$CODEX_HOME/runcat-refresh.stdout.log" "$CODEX_HOME/runcat-refresh.stderr.log" <<'PY'
import plistlib
import sys
from pathlib import Path

plist_path, python_bin, hook_path, stdout_path, stderr_path = sys.argv[1:]

payload = {
    "Label": "dev.runcat.codex-usage",
    "ProgramArguments": [python_bin, hook_path, "--refresh"],
    "StartInterval": 300,
    "RunAtLoad": True,
    "StandardOutPath": stdout_path,
    "StandardErrorPath": stderr_path,
}

with Path(plist_path).open("wb") as f:
    plistlib.dump(payload, f)
PY

# Refresh once now, then register the five-minute background job.
"$PYTHON_BIN" "$HOOK_DST" --refresh || true
launchctl bootout "gui/$(id -u)/$REFRESH_LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$REFRESH_PLIST"

echo
echo "Installed Codex -> RunCat Neo integration:"
echo "  Stop hook: immediate refresh after Codex turns"
echo "  Background: refresh every 5 minutes"
echo
echo "Hook:"
echo "  $HOOK_DST"
echo "LaunchAgent:"
echo "  $REFRESH_PLIST"
echo
echo "If RunCat Neo already watches ~/.codex/runcat-usage.json, no re-adding is needed."
echo
echo "Optional manual refresh:"
echo "  python3 $HOOK_DST --refresh"
