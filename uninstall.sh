#!/bin/sh
set -eu

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
HOOK_DST="$CODEX_HOME/runcat-neo-hook.py"
HOOKS_JSON="$CODEX_HOME/hooks.json"
STAMP=$(date +%Y%m%d-%H%M%S)

if [ -f "$HOOKS_JSON" ]; then
  cp -p "$HOOKS_JSON" "$HOOKS_JSON.backup-$STAMP"

  HOOK_DST="$HOOK_DST" HOOKS_JSON="$HOOKS_JSON" python3 - <<'PY'
import json
import os
from pathlib import Path

hook_path = os.environ["HOOK_DST"]
hooks_path = Path(os.environ["HOOKS_JSON"])

try:
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
except Exception as exc:
    raise SystemExit(f"Could not read {hooks_path}: {exc}")

hooks = data.get("hooks")
if isinstance(hooks, dict):
    stop = hooks.get("Stop")
    if isinstance(stop, list):
        new_stop = []
        for group in stop:
            if not isinstance(group, dict):
                new_stop.append(group)
                continue

            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                new_stop.append(group)
                continue

            filtered = [
                handler
                for handler in handlers
                if not (
                    isinstance(handler, dict)
                    and handler.get("type") == "command"
                    and handler.get("command") == hook_path
                )
            ]

            if filtered:
                updated = dict(group)
                updated["hooks"] = filtered
                new_stop.append(updated)

        if new_stop:
            hooks["Stop"] = new_stop
        else:
            hooks.pop("Stop", None)

hooks_path.write_text(
    json.dumps(data, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
PY
fi

rm -f "$HOOK_DST"

echo "Removed Codex → RunCat Neo hook registration and script."
echo
echo "Generated data was kept:"
echo "  $CODEX_HOME/runcat-usage.json"
echo
echo "Delete that file manually if you no longer want RunCat Neo to read it."
