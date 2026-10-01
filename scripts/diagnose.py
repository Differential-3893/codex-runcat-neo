#!/usr/bin/env python3
"""Print a privacy-filtered Codex account/rate-limit diagnostic.

This intentionally omits email, access tokens, account IDs, user IDs, and
transcript contents.
"""

from __future__ import annotations

import json
import select
import shutil
import subprocess
import time
from pathlib import Path


def find_codex() -> str:
    preferred = Path.home() / ".local" / "bin" / "codex"
    if preferred.exists():
        return str(preferred)

    found = shutil.which("codex")
    if found:
        return found

    raise SystemExit("Codex CLI not found")


def send(proc, message):
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def wait_for(proc, request_id, deadline):
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for Codex app-server")

        ready, _, _ = select.select([proc.stdout], [], [], remaining)
        if not ready:
            raise TimeoutError("Timed out waiting for Codex app-server")

        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("Codex app-server exited")

        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        if message.get("id") != request_id:
            continue

        if "error" in message:
            raise RuntimeError(message["error"])

        return message.get("result") or {}


codex = find_codex()

proc = subprocess.Popen(
    [codex, "app-server", "--listen", "stdio://"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    encoding="utf-8",
    bufsize=1,
)

deadline = time.monotonic() + 5.0

try:
    send(
        proc,
        {
            "id": "init",
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "runcat-diagnostic",
                    "title": "RunCat Diagnostic",
                    "version": "1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        },
    )
    wait_for(proc, "init", deadline)
    send(proc, {"method": "initialized"})

    send(
        proc,
        {
            "id": "usage",
            "method": "account/rateLimits/read",
            "params": {
                "supportsLunaReserve": False,
                "excludeResetCreditDetails": False,
            },
        },
    )
    usage = wait_for(proc, "usage", deadline)
finally:
    try:
        proc.terminate()
        proc.wait(timeout=0.5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

limits = usage.get("rateLimits") or {}
credits = limits.get("credits")
reset_info = usage.get("rateLimitResetCredits")

safe = {
    "planType": limits.get("planType"),
    "primary": limits.get("primary"),
    "secondary": limits.get("secondary"),
    "credits": (
        {key: credits.get(key) for key in ("hasCredits", "unlimited", "balance")}
        if isinstance(credits, dict)
        else None
    ),
    "resetCoupons": None,
}

if isinstance(reset_info, dict):
    safe["resetCoupons"] = {
        "availableCount": reset_info.get("availableCount"),
        "credits": [],
    }

    for credit in reset_info.get("credits") or []:
        if not isinstance(credit, dict):
            continue

        safe["resetCoupons"]["credits"].append(
            {
                "resetType": credit.get("resetType"),
                "status": credit.get("status"),
                "grantedAt": credit.get("grantedAt"),
                "expiresAt": credit.get("expiresAt"),
                "title": credit.get("title"),
                "description": credit.get("description"),
            }
        )

print(json.dumps(safe, indent=2, ensure_ascii=False))
