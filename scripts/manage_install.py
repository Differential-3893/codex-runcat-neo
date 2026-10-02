#!/usr/bin/env python3
"""Transactional, user-level RunCat installation. No sudo and no account changes."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LABEL = "dev.runcat.codex-usage"
ROOT = Path(__file__).resolve().parents[1]


def atomic_bytes(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def executable(value: str) -> str:
    resolved = shutil.which(value)
    if not resolved:
        raise RuntimeError("Required executable not found; nothing was installed.")
    # Keep a stable Homebrew opt/bin symlink, not a version-specific Cellar path.
    return os.path.abspath(resolved)


def read_hooks(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (ValueError, UnicodeError):
        raise RuntimeError("Invalid hooks.json; existing configuration was not changed.") from None
    if not isinstance(data, dict):
        raise RuntimeError("hooks.json must be an object; nothing was changed.")
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict) or not isinstance(hooks.get("Stop", []), list):
        raise RuntimeError("Invalid hooks.Stop configuration; nothing was changed.")
    return data


def owned_handler(handler: object, script: Path, wrapper: Path) -> bool:
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return False
    command = handler.get("command")
    if command in (str(script), str(wrapper)):
        return True  # also migrate the legacy, unquoted script command
    if not isinstance(command, str):
        return False
    try:
        return shlex.split(command) in ([str(script)], [str(wrapper)])
    except ValueError:
        return False


def remove_owned(data: dict, script: Path, wrapper: Path) -> None:
    hooks = data.get("hooks", {})
    old = hooks.get("Stop")
    if not isinstance(old, list):
        return
    new = []
    for group in old:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            new.append(group)
            continue
        remaining = [h for h in group["hooks"] if not owned_handler(h, script, wrapper)]
        if len(remaining) == len(group["hooks"]):
            new.append(group)  # preserve unrelated empty groups, too
        elif remaining:
            new.append({**group, "hooks": remaining})
    if new:
        hooks["Stop"] = new
    else:
        hooks.pop("Stop", None)


def launch(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=check, timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    args = parser.parse_args()
    if subprocess.check_output(["uname", "-s"], text=True).strip() != "Darwin":
        raise RuntimeError("This integration requires macOS.")

    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser().absolute()
    script = codex_home / "runcat-neo-hook.py"
    wrapper = codex_home / "runcat-neo-hook.sh"
    hooks_path = codex_home / "hooks.json"
    plist_path = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    gui = f"gui/{os.getuid()}"
    service = f"{gui}/{LABEL}"
    data = read_hooks(hooks_path)  # Validate before modifying any installed file.
    old_plist = None
    if plist_path.exists():
        try:
            old_plist = plistlib.loads(plist_path.read_bytes())
            installed_home = Path(old_plist["ProgramArguments"][1]).parent
        except (ValueError, TypeError, KeyError, IndexError):
            raise RuntimeError("Existing LaunchAgent is invalid; nothing was changed.") from None
        if installed_home != codex_home:
            raise RuntimeError("LaunchAgent uses another CODEX_HOME; use that same CODEX_HOME first.")

    remove_owned(data, script, wrapper)
    writes: dict[Path, tuple[bytes, int] | None] = {}
    if args.action == "install":
        python = executable(os.environ.get("RUNCAT_PYTHON_BIN") or sys.executable)
        spec = importlib.util.spec_from_file_location("runcat_install", ROOT / "runcat-neo-hook.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("Producer is missing from this download.")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        codex = executable(hook.find_codex())
        environment = {"CODEX_HOME": str(codex_home), "CODEX_BIN": codex,
                       "PATH": os.environ.get("PATH", os.defpath)}
        if os.environ.get("RUNCAT_OUT_FILE"):
            environment["RUNCAT_OUT_FILE"] = str(Path(os.environ["RUNCAT_OUT_FILE"]).expanduser().absolute())
        wrapper_text = "#!/bin/sh\nset -eu\n" + "".join(
            f"export {key}={shlex.quote(value)}\n" for key, value in environment.items()
        ) + f"exec {shlex.quote(python)} {shlex.quote(str(script))} \"$@\"\n"
        data.setdefault("hooks", {}).setdefault("Stop", []).append({"hooks": [
            {"type": "command", "command": shlex.quote(str(wrapper)), "timeout": 5}
        ]})
        payload = {
            "Label": LABEL, "ProgramArguments": [python, str(script), "--refresh"],
            "EnvironmentVariables": environment,
            "StartInterval": 300, "RunAtLoad": True,
            "StandardOutPath": str(codex_home / "runcat-refresh.stdout.log"),
            "StandardErrorPath": str(codex_home / "runcat-refresh.stderr.log"),
        }
        writes[script] = ((ROOT / "runcat-neo-hook.py").read_bytes(), 0o755)
        writes[wrapper] = (wrapper_text.encode("utf-8"), 0o755)
        writes[plist_path] = (plistlib.dumps(payload), 0o644)
    else:
        writes.update({script: None, wrapper: None, plist_path: None})
    if args.action == "install" or hooks_path.exists():
        writes[hooks_path] = ((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
                             (hooks_path.stat().st_mode & 0o777) if hooks_path.exists() else 0o600)

    # Save exact pre-install bytes. Failed launchd registration restores these.
    before = {}
    for path in writes:
        if path.is_symlink():
            raise RuntimeError("An installation target is a symlink; nothing was changed.")
        before[path] = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
    codex_home.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="runcat-backup-", dir=str(codex_home)))
    for index, (path, content) in enumerate(before.items()):
        if content is not None:
            atomic_bytes(backup / f"{index}-{path.name}", content[0], 0o600)
    atomic_bytes(backup / "manifest.json", json.dumps({str(path): f"{i}-{path.name}" if value else None
                 for i, (path, value) in enumerate(before.items())}, indent=2).encode(), 0o600)
    was_loaded = launch("print", service, check=False).returncode == 0
    launch("bootout", service, check=False)
    try:
        for path, content in writes.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_bytes(path, *content)
        if args.action == "install":
            launch("bootstrap", gui, str(plist_path))
    except Exception:
        launch("bootout", service, check=False)
        for path, content in before.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_bytes(path, *content)
        if was_loaded and old_plist is not None:
            launch("bootstrap", gui, str(plist_path), check=False)
        raise RuntimeError("Installation failed; previous files restored. Backup: " + str(backup)) from None

    print("Backup: " + str(backup))
    if args.action == "uninstall":
        print("Uninstalled RunCat Codex hook and LaunchAgent. Snapshot and logs were kept.")
        return 0
    # RunAtLoad already started the first refresh; avoid a redundant account query.
    print("Installed: Stop hook + 300-second LaunchAgent.")
    print("Metric: " + environment.get("RUNCAT_OUT_FILE", str(codex_home / "runcat-usage.json")))
    print("Run scripts/verify_local.py to verify a fresh account snapshot.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print("RunCat setup: " + (str(exc) if isinstance(exc, RuntimeError) else "system operation failed"), file=sys.stderr)
        raise SystemExit(1)
