#!/usr/bin/env python3
"""Verify the installed producer without printing an account response or tokens."""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "dev.runcat.codex-usage"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    plist_path = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    config = plistlib.loads(plist_path.read_bytes())
    args = config.get("ProgramArguments")
    require(isinstance(args, list) and len(args) == 3 and args[-1] == "--refresh", "LaunchAgent command is not the expected refresh command.")
    script = Path(args[1])
    home = script.parent
    require(script.name == "runcat-neo-hook.py", "Unexpected installed script.")
    require(hashlib.sha256(script.read_bytes()).digest() == hashlib.sha256((ROOT / script.name).read_bytes()).digest(), "Installed producer does not match this package. Run install.sh first.")
    require(config.get("Label") == LABEL and config.get("StartInterval") == 300, "LaunchAgent cadence or label does not match.")
    environment = config.get("EnvironmentVariables", {})
    require(environment.get("CODEX_HOME") == str(home), "LaunchAgent CODEX_HOME is inconsistent.")
    registered = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    require(registered.returncode == 0, "LaunchAgent is not registered in the current GUI session.")
    print("PASS: installed source matches; LaunchAgent is registered at 300 seconds.")

    env = {**os.environ, **environment}
    refreshed = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=6)
    require(refreshed.returncode == 0, "Account refresh failed. Previous snapshot was kept; check Codex login/network, then run this check again.")
    out = Path(environment.get("RUNCAT_OUT_FILE", str(home / "runcat-usage.json")))
    value = json.loads(out.read_text(encoding="utf-8"))
    require(value.get("title") == "Codex" and value.get("symbol") == "apple.terminal", "Unexpected metric card format.")
    require(isinstance(value.get("metrics"), list) and any(isinstance(m, dict) and "normalizedValue" in m for m in value["metrics"]), "No quota metric in the fresh snapshot.")
    observed = datetime.fromisoformat(value["lastUpdatedDate"].replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - observed).total_seconds()
    require(-5 <= age <= 30, "Snapshot is not freshly updated.")
    print("PASS: live account refresh wrote a fresh metric snapshot.")

    wrapper = home / "runcat-neo-hook.sh"
    hooks = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
    matches = []
    for group in hooks.get("hooks", {}).get("Stop", []):
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        for handler in group["hooks"]:
            if isinstance(handler, dict) and handler.get("type") == "command":
                try:
                    if shlex.split(handler.get("command", "")) == [str(wrapper)]:
                        matches.append(handler)
                except (ValueError, TypeError):
                    pass
    require(len(matches) == 1 and matches[0].get("timeout") == 5, "Expected one Stop hook with a five-second timeout.")
    response = subprocess.run([str(wrapper)], input="{}\n", env=env, capture_output=True, text=True, timeout=6)
    require(response.returncode == 0 and response.stdout.strip() == "{}" and not response.stderr, "Installed Stop command failed its manual JSON-response check.")
    print("PASS: one Stop hook is registered; its manual JSON-response check passed.")
    print("LOCAL CHECK PASSED. In RunCat, keep using the same Custom Metrics file.")
    print("This checks a manual hook call, not a real Codex turn or a full five-minute timer cycle.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("CHECK FAILED: " + (str(exc) if isinstance(exc, RuntimeError) else "could not read or run the installed integration"), file=sys.stderr)
        raise SystemExit(1)
