#!/bin/sh
set -eu
python3 - <<'PY'
import json
import os
import plistlib
import subprocess
from pathlib import Path

plist = Path.home() / "Library/LaunchAgents/dev.runcat.codex-usage.plist"
config = plistlib.loads(plist.read_bytes())
environment = config.get("EnvironmentVariables", {})
home = Path(environment.get("CODEX_HOME", str(Path.home() / ".codex")))
wrapper = home / "runcat-neo-hook.sh"
if not wrapper.is_file():
    raise SystemExit("Install the updated integration first.")
latest = None
mtime = -1
for path in (home / "sessions").rglob("*.jsonl"):
    try:
        stamp = path.stat().st_mtime_ns
        if path.is_file() and stamp > mtime:
            latest, mtime = path, stamp
    except OSError:
        continue
if latest is None:
    raise SystemExit("No Codex transcript found.")
# json.dumps handles quotes, backslashes and non-ASCII filenames correctly.
subprocess.run([str(wrapper)], input=json.dumps({"transcript_path": str(latest)}),
               text=True, check=True, timeout=6, env={**os.environ, **environment})
out = Path(environment.get("RUNCAT_OUT_FILE", str(home / "runcat-usage.json")))
print(json.dumps(json.loads(out.read_text(encoding="utf-8")), indent=2, ensure_ascii=False))
PY
