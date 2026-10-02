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
from xml.parsers.expat import ExpatError

MIN_PYTHON = (3, 9)
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


def nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise RuntimeError(name + " must be a nonempty string; nothing was changed.")
    return value


def absolute_path(value: object, name: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(nonempty(value, name))))


def read_installation(path: Path) -> dict | None:
    if path.is_symlink():
        raise RuntimeError("LaunchAgent is a symlink; nothing was changed.")
    if not path.exists():
        return None
    try:
        data = plistlib.loads(path.read_bytes())
    except (ValueError, TypeError, ExpatError):
        raise RuntimeError("Existing LaunchAgent is invalid; nothing was changed.") from None
    if not isinstance(data, dict) or data.get("Label") != LABEL:
        raise RuntimeError("Unexpected LaunchAgent label/shape; nothing was changed.")
    args = data.get("ProgramArguments")
    if (not isinstance(args, list) or len(args) != 3 or args[-1] != "--refresh"
            or not all(isinstance(v, str) and v.strip() for v in args)):
        raise RuntimeError("Unexpected LaunchAgent command; nothing was changed.")
    script = Path(args[1])
    if not Path(args[0]).is_absolute():
        raise RuntimeError("Recorded Python must have an absolute path; nothing was changed.")
    if not script.is_absolute() or script.name != "runcat-neo-hook.py":
        raise RuntimeError("Unexpected installed script; nothing was changed.")
    env = data.get("EnvironmentVariables", {})
    if not isinstance(env, dict):
        raise RuntimeError("Invalid LaunchAgent environment; nothing was changed.")
    for key in ("CODEX_HOME", "CODEX_BIN", "PATH", "RUNCAT_OUT_FILE"):
        if key in env:
            nonempty(env[key], "Recorded " + key)
    if "CODEX_HOME" in env and absolute_path(env["CODEX_HOME"], "CODEX_HOME") != absolute_path(script.parent.as_posix(), "script directory"):
        raise RuntimeError("LaunchAgent CODEX_HOME disagrees with its script; nothing was changed.")
    return data


def setting(key: str, old: dict, default: object) -> str:
    # An empty override is an error, not an implicit request to forget a value.
    if key in os.environ:
        return nonempty(os.environ[key], key)
    if key in old:
        return nonempty(old[key], "Recorded " + key)
    return nonempty(default, key)


def wrapper_content(python: str, script: Path, environment: dict[str, str]) -> bytes:
    # Stable order lets the verifier check that the Stop hook and launchd have
    # exactly the same saved runtime, independent of plist dictionary order.
    text = "#!/bin/sh\nset -eu\n" + "".join(
        f"export {key}={shlex.quote(value)}\n" for key, value in sorted(environment.items())
    ) + f'exec {shlex.quote(python)} {shlex.quote(str(script))} "$@"\n'
    return text.encode("utf-8")


def check_python(python: str) -> None:
    result = subprocess.run([python, "-c", f"import sys; sys.exit(0 if sys.version_info >= {MIN_PYTHON!r} else 1)"],
                            capture_output=True, timeout=5)
    if result.returncode:
        raise RuntimeError("The selected runtime requires Python 3.9 or newer; nothing was changed.")


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
    parser.add_argument("--bootstrap-python", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if subprocess.check_output(["uname", "-s"], text=True).strip() != "Darwin":
        raise RuntimeError("This integration requires macOS.")

    home = Path.home()
    plist_path = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    old_plist = read_installation(plist_path)
    old_env = old_plist.get("EnvironmentVariables", {}) if old_plist else {}
    installed_home = (absolute_path(str(Path(old_plist["ProgramArguments"][1]).parent), "installed home")
                      if old_plist else None)
    codex_home = absolute_path(setting("CODEX_HOME", old_env,
                                      str(installed_home or home / ".codex")), "CODEX_HOME")
    if installed_home is not None and installed_home != codex_home:
        raise RuntimeError("LaunchAgent uses another CODEX_HOME; uninstall that installation before moving it.")
    script = codex_home / "runcat-neo-hook.py"
    wrapper = codex_home / "runcat-neo-hook.sh"
    hooks_path = codex_home / "hooks.json"
    gui = f"gui/{os.getuid()}"
    service = f"{gui}/{LABEL}"
    data = read_hooks(hooks_path)  # Validate before modifying any installed file.

    remove_owned(data, script, wrapper)
    writes: dict[Path, tuple[bytes, int] | None] = {}
    if args.action == "install":
        saved_python = old_plist["ProgramArguments"][0] if old_plist else (args.bootstrap_python or sys.executable)
        python = executable(setting("RUNCAT_PYTHON_BIN", {}, saved_python))
        check_python(python)
        spec = importlib.util.spec_from_file_location("runcat_install", ROOT / "runcat-neo-hook.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("Producer is missing from this download.")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        # Do not silently rediscover another Codex/Python after a saved binary
        # disappears. The caller must explicitly replace that setting.
        if "CODEX_BIN" in os.environ or "CODEX_BIN" in old_env:
            codex = executable(setting("CODEX_BIN", old_env, "codex"))
        else:
            codex = executable(hook.find_codex())
        runtime_path = setting("RUNCAT_RUNTIME_PATH", {},
                               old_env.get("PATH", os.environ.get("PATH", os.defpath)))
        output = absolute_path(setting("RUNCAT_OUT_FILE", old_env,
                                       str(codex_home / "runcat-usage.json")), "RUNCAT_OUT_FILE")
        protected = (script, wrapper, hooks_path, plist_path,
                     codex_home / "runcat-refresh.stdout.log", codex_home / "runcat-refresh.stderr.log",
                     codex_home / "auth.json", codex_home / "config.toml",
                     Path(python), Path(codex))
        if output.resolve() in {p.resolve() for p in protected}:
            raise RuntimeError("Metric output conflicts with an installed file; nothing was changed.")
        if output.is_symlink() or (output.exists() and not output.is_file()):
            raise RuntimeError("Metric output is a symlink or non-file; nothing was changed.")
        environment = {"CODEX_HOME": str(codex_home), "CODEX_BIN": codex,
                       "PATH": runtime_path, "RUNCAT_OUT_FILE": str(output)}
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
        writes[wrapper] = (wrapper_content(python, script, environment), 0o755)
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
    if was_loaded:
        # A failed stop is not permission to replace a still-running installation.
        stopped = launch("bootout", service, check=False)
        if stopped.returncode or launch("print", service, check=False).returncode == 0:
            raise RuntimeError("Could not stop the existing LaunchAgent; files unchanged. Backup: " + str(backup))
    try:
        for path, content in writes.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_bytes(path, *content)
        if args.action == "install":
            launch("bootstrap", gui, str(plist_path))
    except BaseException:
        problems = []
        try:
            launch("bootout", service, check=False)
        except Exception:
            problems.append("stopping partial installation")
        for path, content in before.items():
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_bytes(path, *content)
            except Exception:
                problems.append("restoring target files")
        if was_loaded and old_plist is not None:
            try:
                launch("bootstrap", gui, str(plist_path))
            except Exception:
                problems.append("reloading previous job")
        if problems:
            raise RuntimeError("Installation failed; RESTORE INCOMPLETE. Keep backup: " + str(backup)) from None
        raise RuntimeError("Installation failed; previous files and loaded state restored. Backup: " + str(backup)) from None

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
