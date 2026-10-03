#!/usr/bin/env python3
"""Verify the installed producer without printing an account response or tokens."""
from __future__ import annotations

import hashlib
import json
import math
import os
import plistlib
import shlex
import stat
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from manage_install import wrapper_content

ROOT = Path(__file__).resolve().parents[1]
LABEL = "dev.runcat.codex-usage"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)



MAX_SNAPSHOT_BYTES = 1024 * 1024


def snapshot_fd(path: Path, *, missing_ok: bool = False):
    """Open without following symlinks/blocking on a FIFO; never change data."""
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise RuntimeError("No snapshot was created by the check.") from None
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), "Snapshot is not a regular file.")
    except BaseException:
        os.close(fd)
        raise
    return fd


def fresh_snapshot(command: list[str], env: dict, out: Path, *, stop: bool = False) -> dict:
    """Require per-invocation success AND a fresh, atomically replaced card.

    Stop uses an opt-in strict exit status, not the normal fail-open response.
    Thus an unrelated writer cannot mask a failed Stop query. Holding the old
    file open prevents inode reuse; identical bytes/timestamps in the same
    second remain valid when atomic_write_json replaced the file.
    """
    before = snapshot_fd(out, missing_ok=True)
    try:
        started = time.time()
        result = subprocess.run(command, input="{}\n" if stop else None,
                                env=env, capture_output=True, text=True, timeout=6)
        finished = time.time()
        if stop:
            require(result.returncode == 0 and result.stdout.strip() == "{}" and not result.stderr,
                    "Stop-hook verification failed: this call did not confirm a fresh write. "
                    "Check Codex login/network and rerun verification; do not treat {} alone as success.")
        else:
            require(result.returncode == 0 and not result.stderr,
                    "Account refresh failed. Previous snapshot was kept; check Codex login/network, "
                    "then run this check again.")
        after = snapshot_fd(out)
        with os.fdopen(after, "rb") as stream:
            info = os.fstat(stream.fileno())
            if before is not None:
                old = os.fstat(before)
                require((old.st_dev, old.st_ino) != (info.st_dev, info.st_ino),
                        "No atomic snapshot replacement was observed during this call.")
            raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
        require(len(raw) <= MAX_SNAPSHOT_BYTES, "Snapshot is too large to verify.")
        value = json.loads(raw)
        require(isinstance(value, dict) and value.get("title") == "Codex"
                and value.get("symbol") == "apple.terminal", "Unexpected metric card format.")
        metrics = value.get("metrics")
        normalized = [m.get("normalizedValue") for m in metrics if isinstance(m, dict)] if isinstance(metrics, list) else []
        require(any(type(n) in (int, float) and 0 <= n <= 1 for n in normalized),
                "No valid quota metric in the new snapshot.")
        stamp = value.get("lastUpdatedDate")
        require(isinstance(stamp, str), "Snapshot timestamp is missing.")
        observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        require(observed.tzinfo is not None, "Snapshot timestamp lacks a timezone.")
        seconds = observed.timestamp()
        require(math.floor(started) <= seconds <= math.ceil(finished)
                and -5 <= finished - seconds <= 30, "Snapshot is not fresh for this invocation.")
        return value
    finally:
        if before is not None:
            os.close(before)


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
    require(isinstance(environment, dict), "LaunchAgent environment is invalid.")
    require(environment.get("CODEX_HOME") == str(home), "LaunchAgent CODEX_HOME is inconsistent.")
    require(all(isinstance(environment.get(k), str) and environment[k] for k in
                ("CODEX_HOME", "CODEX_BIN", "PATH", "RUNCAT_OUT_FILE")), "Saved runtime settings are incomplete.")
    require(set(environment) == {"CODEX_HOME", "CODEX_BIN", "PATH", "RUNCAT_OUT_FILE"},
            "Unexpected variables in the generated LaunchAgent environment.")
    wrapper = home / "runcat-neo-hook.sh"
    require(wrapper.read_bytes() == wrapper_content(args[0], script, environment),
            "Stop hook runtime does not match the LaunchAgent; reinstall this package.")
    registered = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    require(registered.returncode == 0, "LaunchAgent is not registered in the current GUI session.")
    print("PASS: installed source matches; LaunchAgent is registered at 300 seconds.")

    env = {**os.environ, **environment}
    out = Path(environment["RUNCAT_OUT_FILE"])
    fresh_snapshot(args, env, out)
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
    # Same installed launcher, stdin and Stop execution path; only the manual
    # check opts into a failure status when write_snapshot returned False.
    # The actual Codex registration above remains flag-free/fail-open.
    fresh_snapshot([str(wrapper), "--verify-stop"], env, out, stop=True)
    print("PASS: one Stop hook is registered; manual Stop wrote a fresh metric snapshot.")
    print("LOCAL CHECK PASSED. In RunCat, keep using the same Custom Metrics file.")
    print("This checks a manual strict Stop call, not a real Codex turn or a full five-minute timer cycle.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("CHECK FAILED: " + (str(exc) if isinstance(exc, RuntimeError) else "could not read or run the installed integration"), file=sys.stderr)
        raise SystemExit(1)
