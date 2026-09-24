# Maintenance notes

This file is intentionally written as an AI/human handoff.

## Current architecture

```text
Codex turn completes
        |
        v
user-level Stop hook
~/.codex/runcat-neo-hook.py
        |
        +--> transcript_path supplied by Codex
        |      |
        |      +--> latest payload.type == "token_count"
        |             |
        |             +--> rate_limits.primary / secondary
        |             +--> plan_type fallback
        |
        +--> local `codex app-server --listen stdio://`
               |
               +--> account/rateLimits/read
                      |
                      +--> planType
                      +--> rateLimitResetCredits
        |
        v
~/.codex/runcat-usage.json
        |
        v
RunCat Neo Custom Metrics
```

## User-facing semantics

The integration currently prefers:

- plan name,
- remaining quota rather than used quota,
- reset time,
- available reset-coupon count,
- earliest available coupon expiry.

It intentionally does not show:

- current model,
- per-session context-window usage,
- email/account identity.

## Current plan mapping

```text
free      -> Free
plus      -> Plus
prolite   -> Pro Lite
pro       -> Pro
business  -> Business
```

Do not relabel `prolite`/`pro` as community nicknames such as “5x” or “20x”.
Unknown future backend values should be preserved rather than guessed.

## Verified protocol fields

The integration currently understands token-count transcript fields in the
historical snake_case shape:

```json
{
  "rate_limits": {
    "primary": {
      "used_percent": 61.0,
      "window_minutes": 10080,
      "resets_at": 1790391033
    },
    "secondary": null,
    "plan_type": "pro"
  }
}
```

The account app-server currently returns camelCase protocol fields such as:

```json
{
  "rateLimits": {
    "planType": "pro",
    "primary": {
      "usedPercent": 61,
      "windowDurationMins": 10080,
      "resetsAt": 1790391033
    }
  },
  "rateLimitResetCredits": {
    "availableCount": 2,
    "credits": [
      {
        "status": "available",
        "expiresAt": 1791173986
      }
    ]
  }
}
```

Treat these as observed protocol shapes, not permanent contracts.

## If Codex changes quota policy

Do not start by rewriting the integration from memory.

1. Run:

   ```bash
   python3 scripts/diagnose.py
   ```

2. Inspect the latest transcript separately if needed:

   ```bash
   transcript="$(
     find ~/.codex/sessions -name '*.jsonl' -type f -print0 \
     | xargs -0 stat -f '%m %N' \
     | sort -nr \
     | head -n 1 \
     | cut -d ' ' -f 2-
   )"
   echo "$transcript"
   ```

3. Determine:
   - whether the main quota is still in `primary`/`secondary`,
   - whether the duration changed,
   - whether `usedPercent` is still “used” rather than “remaining”,
   - whether reset-credit fields changed,
   - whether plan values changed.

4. Make the smallest schema-specific change.

5. Preserve `~/.codex/runcat-usage.json` unless there is a strong reason not to.

6. Run:

   ```bash
   ./scripts/test-latest.sh
   ```

7. Verify the RunCat card visually.

## Account switching invariant

Never allow stale reset-credit metadata from account A to be displayed as if it
belongs to account B.

The current implementation therefore reads account metadata on each Stop hook
run and does not fall back to cached account/coupon data.

If caching is introduced later, it must be keyed by a reliably detected account
identity and must not persist raw email/access-token data.

## Security invariant

Never print, persist, commit, or log:

- access tokens,
- refresh tokens,
- raw JWTs,
- email addresses,
- ChatGPT account IDs,
- ChatGPT user IDs,
- conversation contents.

The diagnostic script is intentionally filtered.

## Upstream source locations

OpenAI Codex:

- `codex-rs/protocol/src/account.rs`
- `codex-rs/app-server-protocol/src/protocol/v2/account.rs`
- `codex-rs/backend-client/src/client/rate_limit_resets.rs`
- `codex-rs/tui/src/chatwidget/reset_credits.rs`

RunCat Neo:

- <https://github.com/runcat-dev/RunCatNeo>
