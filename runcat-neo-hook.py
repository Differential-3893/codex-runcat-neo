#!/usr/bin/env python3
"""RunCat Neo Custom Metrics producer for OpenAI Codex.

Two refresh paths share the same producer:
- Codex Stop hook: refreshes immediately after a Codex turn and may use the
  turn transcript for the freshest quota snapshot.
- ``--refresh``: refreshes from the currently active Codex account without a
  transcript, intended for a lightweight launchd job every few minutes.

No access token, email address, account id, or transcript content is written to
the RunCat metrics file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import select
import shutil
import subprocess
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

CODEX_HOME = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().absolute()
OUT = Path(
    os.environ.get(
        "RUNCAT_OUT_FILE",
        str(CODEX_HOME / "runcat-usage.json"),
    )
).expanduser().absolute()
ACCOUNT_RPC_TIMEOUT_SECONDS = 3.0
MAX_RPC_LINE_BYTES = 1024 * 1024
MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, allow_nan=False)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def finite_number(value: Any) -> bool:
    """JSON booleans, NaN and infinity are not usable quota measurements."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def latest_token_count(transcript_path: str | None) -> dict[str, Any] | None:
    if not isinstance(transcript_path, str) or not transcript_path:
        return None

    # Only inspect a bounded tail: a long conversation must not delay Stop.
    deadline = time.monotonic() + 0.35
    try:
        fd = os.open(transcript_path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as transcript:
            info = os.fstat(transcript.fileno())
            if not stat.S_ISREG(info.st_mode):
                return None
            start = max(0, info.st_size - MAX_TRANSCRIPT_BYTES)
            transcript.seek(start)
            tail = transcript.read(MAX_TRANSCRIPT_BYTES)
        if start:
            # Discard the possibly truncated first record, not its suffix.
            tail = tail.partition(b"\n")[2]
        for line in reversed(tail.splitlines()):
            if time.monotonic() >= deadline:
                break
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError, RecursionError):
                continue
            if not isinstance(event, dict):
                continue
            payload = event.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "token_count":
                return payload
    except (OSError, ValueError):
        pass
    return None


def transcript_rate_limits(token_count: dict[str, Any] | None) -> dict[str, Any]:
    rate_limits = token_count.get("rate_limits") if isinstance(token_count, dict) else None
    return rate_limits if isinstance(rate_limits, dict) else {}


def transcript_windows(token_count: dict[str, Any] | None) -> list[dict[str, Any]]:
    rate_limits = transcript_rate_limits(token_count)
    windows: list[dict[str, Any]] = []

    for key in ("primary", "secondary"):
        raw = rate_limits.get(key)
        if not isinstance(raw, dict):
            continue

        windows.append(
            {
                "usedPercent": raw.get("used_percent"),
                "windowDurationMins": raw.get("window_minutes"),
                "resetsAt": (
                    raw.get("resets_at")
                    if raw.get("resets_at") is not None
                    else raw.get("reset_at")
                ),
            }
        )

    return windows


def transcript_plan_type(token_count: dict[str, Any] | None) -> str | None:
    value = transcript_rate_limits(token_count).get("plan_type")
    return value if isinstance(value, str) and value.strip() else None


def select_main_quota_window(windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the longest finite account quota window."""

    usable = [
        window for window in windows
        if isinstance(window, dict) and finite_number(window.get("usedPercent"))
    ]
    if not usable:
        return None

    with_duration = [
        window for window in usable
        if finite_number(window.get("windowDurationMins"))
        and window["windowDurationMins"] > 0
    ]
    if with_duration:
        return max(with_duration, key=lambda item: item["windowDurationMins"])
    return usable[0]


def find_codex() -> str:
    override = os.environ.get("CODEX_BIN")
    if override:
        return override

    preferred = Path.home() / ".local" / "bin" / "codex"
    if preferred.is_file() and os.access(preferred, os.X_OK):
        return str(preferred)

    found = shutil.which("codex")
    if found:
        return found

    raise RuntimeError("Codex CLI not found")


def send_rpc(proc: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise RuntimeError("Codex app-server stdin is unavailable")
    proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
    proc.stdin.flush()


def read_rpc_response(
    proc: subprocess.Popen[bytes],
    request_id: str,
    deadline: float,
    pending: bytearray | None = None,
) -> dict[str, Any]:
    """Read newline-delimited JSON without mixing select and text buffering.

    Reuse ``pending`` across requests on the same process. Both buffered lines
    and partial-line reads are subject to the caller's overall deadline.
    """
    if proc.stdout is None:
        raise RuntimeError("Codex app-server stdout is unavailable")
    if pending is None:
        pending = bytearray()
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex account query timed out")

        newline = pending.find(b"\n")
        if newline >= 0:
            if newline > MAX_RPC_LINE_BYTES:
                raise RuntimeError("Codex app-server response is too large")
            line = bytes(pending[:newline])
            del pending[:newline + 1]
            try:
                message = json.loads(line)
            except (ValueError, UnicodeError, RecursionError):
                continue
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                # Never reflect an untrusted RPC error payload into a log.
                raise RuntimeError("Codex app-server rejected the account query")
            result = message.get("result")
            return result if isinstance(result, dict) else {}

        if len(pending) > MAX_RPC_LINE_BYTES:
            raise RuntimeError("Codex app-server response is too large")
        try:
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                raise TimeoutError("Codex account query timed out")
            chunk = os.read(fd, 65536)
        except (InterruptedError, BlockingIOError):
            continue
        if not chunk:
            raise RuntimeError("Codex app-server exited before a complete response")
        pending.extend(chunk)


def close_process(proc: subprocess.Popen[bytes]) -> None:
    """Reap the child after either a successful query or a failed one."""
    try:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=0.5)
    finally:
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                stream.close()


def safe_account_data(result: dict[str, Any]) -> dict[str, Any]:
    """Project known scalar fields; no nested backend objects or free text."""
    rate_limits = result.get("rateLimits")
    rate_limits = rate_limits if isinstance(rate_limits, dict) else {}
    windows = {}
    for key in ("primary", "secondary"):
        window = rate_limits.get(key)
        windows[key] = (
            {field: window.get(field) if finite_number(window.get(field)) else None
             for field in ("usedPercent", "windowDurationMins", "resetsAt")}
            if isinstance(window, dict) else None
        )
    credits = rate_limits.get("credits")
    safe_credits = None
    if isinstance(credits, dict):
        balance = credits.get("balance")
        # A diagnostic must not print arbitrary text in a balance field.
        try:
            if not isinstance(balance, str) or not Decimal(balance.strip()).is_finite():
                balance = None
        except InvalidOperation:
            balance = None
        safe_credits = {
            "hasCredits": credits.get("hasCredits") is True,
            "unlimited": credits.get("unlimited") is True,
            "balance": balance,
        }
    reset_info = result.get("rateLimitResetCredits")
    safe_resets = None
    if isinstance(reset_info, dict):
        count = reset_info.get("availableCount")
        safe_resets = {
            "availableCount": count if finite_number(count) else None,
            "credits": [],
        }
        raw_credits = reset_info.get("credits")
        for credit in raw_credits if isinstance(raw_credits, list) else []:
            if not isinstance(credit, dict):
                continue
            status = credit.get("status")
            # Only availability and expiry are needed by the card/diagnostic.
            safe_status = "available" if isinstance(status, str) and status.lower() == "available" else "unavailable"
            expiry = credit.get("expiresAt")
            safe_resets["credits"].append({
                "status": safe_status,
                "expiresAt": expiry if finite_number(expiry) else None,
            })
    plan = rate_limits.get("planType")
    # Codex plan identifiers are names, not backend messages or account IDs.
    if not isinstance(plan, str) or not plan.strip():
        plan = None
    return {
        "planType": plan,
        "primary": windows["primary"], "secondary": windows["secondary"],
        "credits": safe_credits, "resetCoupons": safe_resets,
    }


def fetch_account_data() -> dict[str, Any]:
    """Fetch only non-secret account metadata needed for the RunCat card."""

    codex = find_codex()
    proc = subprocess.Popen(
        [codex, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
        env={**os.environ, "CODEX_HOME": str(CODEX_HOME)},
    )

    deadline = time.monotonic() + ACCOUNT_RPC_TIMEOUT_SECONDS
    pending = bytearray()

    try:
        send_rpc(
            proc,
            {
                "id": "init",
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "runcat-neo",
                        "title": "RunCat Neo",
                        "version": "1.1",
                    },
                    "capabilities": {
                        "experimentalApi": True,
                    },
                },
            },
        )
        read_rpc_response(proc, "init", deadline, pending)
        send_rpc(proc, {"method": "initialized"})

        send_rpc(
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
        result = read_rpc_response(proc, "usage", deadline, pending)

        return safe_account_data(result)
    finally:
        close_process(proc)


def account_data() -> dict[str, Any] | None:
    """Read the currently active Codex account every run."""

    try:
        return fetch_account_data()
    except Exception:
        return None


def account_windows(account: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(account, dict):
        return []

    windows = []
    for key in ("primary", "secondary"):
        value = account.get(key)
        if isinstance(value, dict):
            windows.append(value)
    return windows


def quota_title(window_minutes: Any) -> str:
    if window_minutes == 10080:
        return "Weekly Remaining"
    if window_minutes == 1440:
        return "Daily Remaining"

    if finite_number(window_minutes) and window_minutes > 0:
        if window_minutes % 1440 == 0:
            return f"{window_minutes / 1440:g}d Remaining"
        if window_minutes % 60 == 0:
            return f"{window_minutes / 60:g}h Remaining"

    return "Quota Remaining"


def plan_name(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None

    value = raw.strip().lower()
    if not value:
        return None

    # Match TUI SubscriptionDisplay::Status, not KnownPlan::display_name().
    # Include the aliases accepted by PlanType::from_raw_value().
    names = {
        "free": "Free",
        "go": "Go",
        "plus": "Plus",
        "prolite": "Pro 100",
        "pro": "Pro 200",
        "promax": "Pro 500",
        "team": "Business",
        "self_serve_business_usage_based": "Business",
        "business": "Enterprise",
        "self_serve_business_prolite": "Business Premium",
        "ent26": "Enterprise",
        "enterprise_cbp_usage_based": "Enterprise",
        "enterprise": "Enterprise",
        "hc": "Enterprise",
        "enterprise_cbp_automation": "Enterprise (Automation)",
        "edu": "Edu",
        "education": "Edu",
        "edu_plus": "Edu Plus",
        "edu_pro": "Edu Pro",
    }

    if value in names:
        return names[value]

    return raw.strip().replace("_", " ").title()


def local_time_text(timestamp: Any) -> str | None:
    if not finite_number(timestamp):
        return None

    try:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone()
        return f"{dt.month}/{dt.day} {dt:%H:%M}"
    except (ValueError, OSError, OverflowError):
        return None


def credit_metrics(account: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Display only current-account credits; missing balances are not zero."""

    credits = account.get("credits") if isinstance(account, dict) else None
    if not isinstance(credits, dict):
        return []

    value = None
    if credits.get("unlimited") is True:
        value = "Unlimited"
    elif credits.get("hasCredits") is True:
        raw_balance = credits.get("balance")
        if isinstance(raw_balance, str):
            try:
                balance = Decimal(raw_balance.strip())
                if balance.is_finite() and balance > 0:
                    rounded = balance.to_integral_value(rounding=ROUND_HALF_UP)
                    value = format(int(rounded), ",")
            except (InvalidOperation, ValueError, OverflowError):
                pass
        if value is None:
            value = "Available"

    if value is None:
        return []
    return [{"title": "Credits Remaining", "formattedValue": value}]


def coupon_metrics(account: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(account, dict):
        return []

    reset_info = account.get("resetCoupons")
    if not isinstance(reset_info, dict):
        return []

    metrics: list[dict[str, Any]] = []
    count = reset_info.get("availableCount")

    if finite_number(count):
        metrics.append(
            {
                "title": "Reset Coupons",
                "formattedValue": str(max(0, int(count))),
            }
        )

    expiries: list[float] = []
    raw_credits = reset_info.get("credits")
    for credit in raw_credits if isinstance(raw_credits, list) else []:
        if not isinstance(credit, dict):
            continue

        status = credit.get("status")
        if isinstance(status, str) and status.lower() != "available":
            continue

        expires_at = credit.get("expiresAt")
        if finite_number(expires_at):
            expiries.append(float(expires_at))

    if expiries:
        expiry_text = local_time_text(min(expiries))
        if expiry_text:
            metrics.append(
                {
                    "title": "Next Expiry",
                    "formattedValue": expiry_text,
                }
            )

    return metrics


def percentage_text(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") + "%"


def write_snapshot(hook_input: dict[str, Any] | None = None) -> bool:
    """Write a snapshot and return True when quota data was available."""

    hook_input = hook_input if isinstance(hook_input, dict) else {}
    token_count = latest_token_count(hook_input.get("transcript_path"))
    account = account_data()

    # A Stop hook gets the turn-local transcript first for immediate freshness.
    # Background refreshes have no transcript and therefore use account data.
    window = select_main_quota_window(transcript_windows(token_count))
    if window is None:
        window = select_main_quota_window(account_windows(account))
    if window is None:
        return False

    used = window.get("usedPercent")
    if not finite_number(used):
        return False

    remaining = max(0.0, min(100.0, 100.0 - float(used)))
    formatted_remaining = percentage_text(remaining)

    raw_plan = None
    if isinstance(account, dict):
        raw_plan = account.get("planType")
    if not raw_plan:
        raw_plan = transcript_plan_type(token_count)

    metrics: list[dict[str, Any]] = []

    plan = plan_name(raw_plan)
    if plan:
        metrics.append(
            {
                "title": "Plan",
                "formattedValue": plan,
            }
        )

    metrics.append(
        {
            "title": quota_title(window.get("windowDurationMins")),
            "formattedValue": formatted_remaining,
            "normalizedValue": round(remaining / 100.0, 4),
        }
    )

    reset_text = local_time_text(window.get("resetsAt"))
    if reset_text:
        metrics.append(
            {
                "title": "Reset",
                "formattedValue": reset_text,
            }
        )

    metrics.extend(credit_metrics(account))
    metrics.extend(coupon_metrics(account))

    snapshot = {
        "title": "Codex",
        "symbol": "apple.terminal",
        "metrics": metrics,
        "lastUpdatedDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metricsBarValue": formatted_remaining,
    }

    atomic_write_json(OUT, snapshot)
    return True


def refresh_from_account() -> bool:
    """Background refresh path used by launchd; no transcript is required."""

    return write_snapshot({})


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--refresh", action="store_true")
    args, _unknown = parser.parse_known_args()

    if args.refresh:
        try:
            if refresh_from_account():
                return 0
        except Exception:
            pass
        print("RunCat Codex refresh: no fresh snapshot; existing file preserved.", file=sys.stderr)
        return 1

    try:
        hook_input = json.load(sys.stdin)
        if not isinstance(hook_input, dict):
            hook_input = {}
        write_snapshot(hook_input)
    except Exception:
        # No exception text: it can contain input fragments or private paths.
        print("RunCat Codex hook: refresh skipped; existing file preserved.", file=sys.stderr)

    # A failed metric must never block the Codex turn.
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
