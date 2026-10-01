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
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

CODEX_HOME = Path.home() / ".codex"
OUT = Path(
    os.environ.get(
        "RUNCAT_OUT_FILE",
        str(CODEX_HOME / "runcat-usage.json"),
    )
)
ACCOUNT_RPC_TIMEOUT_SECONDS = 3.0


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def latest_token_count(transcript_path: str | None) -> dict[str, Any] | None:
    if not transcript_path:
        return None

    latest = None
    try:
        with Path(transcript_path).open(encoding="utf-8") as transcript:
            for line in transcript:
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue

                payload = event.get("payload") or {}
                if payload.get("type") == "token_count":
                    latest = payload
    except OSError:
        return None

    return latest


def transcript_rate_limits(token_count: dict[str, Any] | None) -> dict[str, Any]:
    rate_limits = (token_count or {}).get("rate_limits")
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

    if not windows:
        return None

    with_duration = [
        window
        for window in windows
        if isinstance(window.get("windowDurationMins"), (int, float))
        and window["windowDurationMins"] > 0
    ]
    if with_duration:
        return max(with_duration, key=lambda item: item["windowDurationMins"])

    return windows[0]


def find_codex() -> str:
    override = os.environ.get("CODEX_BIN")
    if override:
        return override

    preferred = Path.home() / ".local" / "bin" / "codex"
    if preferred.exists():
        return str(preferred)

    found = shutil.which("codex")
    if found:
        return found

    raise RuntimeError("Codex CLI not found")


def send_rpc(proc: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise RuntimeError("Codex app-server stdin is unavailable")
    proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def read_rpc_response(
    proc: subprocess.Popen[str],
    request_id: str,
    deadline: float,
) -> dict[str, Any]:
    if proc.stdout is None:
        raise RuntimeError("Codex app-server stdout is unavailable")

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex account query timed out")

        readable, _, _ = select.select([proc.stdout], [], [], remaining)
        if not readable:
            raise TimeoutError("Codex account query timed out")

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
            raise RuntimeError(str(message["error"]))

        result = message.get("result")
        return result if isinstance(result, dict) else {}


def fetch_account_data() -> dict[str, Any]:
    """Fetch only non-secret account metadata needed for the RunCat card."""

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

    deadline = time.monotonic() + ACCOUNT_RPC_TIMEOUT_SECONDS

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
        read_rpc_response(proc, "init", deadline)
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
        result = read_rpc_response(proc, "usage", deadline)

        rate_limits = result.get("rateLimits")
        rate_limits = rate_limits if isinstance(rate_limits, dict) else {}

        credits = rate_limits.get("credits")
        safe_credits = None
        if isinstance(credits, dict):
            safe_credits = {
                key: credits.get(key) for key in ("hasCredits", "unlimited", "balance")
            }

        reset_info = result.get("rateLimitResetCredits")
        safe_resets = None

        if isinstance(reset_info, dict):
            safe_resets = {
                "availableCount": reset_info.get("availableCount"),
                "credits": [],
            }

            for credit in reset_info.get("credits") or []:
                if not isinstance(credit, dict):
                    continue

                safe_resets["credits"].append(
                    {
                        "status": credit.get("status"),
                        "expiresAt": credit.get("expiresAt"),
                        "title": credit.get("title"),
                    }
                )

        return {
            "planType": rate_limits.get("planType"),
            "primary": rate_limits.get("primary"),
            "secondary": rate_limits.get("secondary"),
            "credits": safe_credits,
            "resetCoupons": safe_resets,
        }

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


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

    if isinstance(window_minutes, (int, float)) and window_minutes > 0:
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

    names = {
        "free": "Free",
        "plus": "Plus",
        "prolite": "Pro",
        "pro": "Pro (More)",
        "promax": "Pro (Max)",
        "business": "Business",
    }

    if value in names:
        return names[value]

    return raw.strip().replace("_", " ").title()


def local_time_text(timestamp: Any) -> str | None:
    if not isinstance(timestamp, (int, float)):
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
    else:
        raw_balance = credits.get("balance")
        if isinstance(raw_balance, str):
            try:
                balance = Decimal(raw_balance.strip())
                if balance.is_finite() and balance >= 0:
                    rounded = balance.to_integral_value(rounding=ROUND_HALF_UP)
                    value = format(int(rounded), ",")
            except InvalidOperation:
                pass
        if value is None and credits.get("hasCredits") is True:
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

    if isinstance(count, (int, float)):
        metrics.append(
            {
                "title": "Reset Coupons",
                "formattedValue": str(max(0, int(count))),
            }
        )

    expiries: list[float] = []
    for credit in reset_info.get("credits") or []:
        if not isinstance(credit, dict):
            continue

        status = credit.get("status")
        if isinstance(status, str) and status.lower() != "available":
            continue

        expires_at = credit.get("expiresAt")
        if isinstance(expires_at, (int, float)):
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
    windows = transcript_windows(token_count)
    if not windows:
        windows = account_windows(account)

    window = select_main_quota_window(windows)
    if window is None:
        return False

    used = window.get("usedPercent")
    if not isinstance(used, (int, float)):
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


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--refresh", action="store_true")
    args, _unknown = parser.parse_known_args()

    if args.refresh:
        try:
            refresh_from_account()
        except Exception as error:
            print(f"RunCat Codex refresh: {error}", file=sys.stderr)
        return

    try:
        hook_input = json.load(sys.stdin)
        if not isinstance(hook_input, dict):
            hook_input = {}
        write_snapshot(hook_input)
    except Exception as error:
        print(f"RunCat Codex hook: {error}", file=sys.stderr)

    # Codex hooks expect a JSON response on stdout.
    print("{}")


if __name__ == "__main__":
    main()
