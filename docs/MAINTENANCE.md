# Maintenance notes

This file is intentionally written as an AI/human handoff.

## Current architecture

```text
A. Immediate path

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

B. Idle/background path

launchd every 300 s
        |
        v
~/.codex/runcat-neo-hook.py --refresh
        |
        v
local `codex app-server --listen stdio://`
        |
        +--> account/rateLimits/read

Both paths
        |
        v
~/.codex/runcat-usage.json
        |
        v
RunCat Neo Custom Metrics
```

The Stop hook prefers transcript quota windows for immediate post-turn freshness.
The background path has no transcript and uses account-level quota windows.

## Refresh invariant

The integration must remain useful even when Codex is not currently being used.

- Stop hook: immediate update after a turn.
- LaunchAgent: account refresh every five minutes.
- Background refresh must not require a transcript.
- A failed account refresh must not destroy a valid existing snapshot.

The LaunchAgent label is:

```text
dev.runcat.codex-usage
```

## User-facing semantics

The integration currently prefers:

- plan name,
- remaining quota rather than used quota,
- reset time,
- current-account credit balance when available,
- available reset-coupon count,
- earliest available coupon expiry.

It intentionally does not show:

- current model,
- per-session context-window usage,
- email/account identity.

The card symbol is currently:

```text
apple.terminal
```

This is a generic SF Symbol, not an official Codex logo.

## Credit balance

Read `rateLimits.credits` from the same `account/rateLimits/read` response.
Keep only `hasCredits`, `unlimited`, and `balance`; do not add another request.
`unlimited: true` takes precedence and displays `Unlimited`, regardless of
`hasCredits`. Otherwise omit the row unless `hasCredits` is true.
`Credits Remaining` displays only finite positive balances numerically in credit
units, rounding to whole credits using `Decimal` with `ROUND_HALF_UP`
(for example, `1250.50` displays `1,251`, and `0.001` displays `0`). When
`hasCredits` is true but the balance is zero, negative, hidden, or invalid,
display `Available`; missing metadata must not be treated as zero.
Never use transcript or cached credits as a fallback across accounts.

## Current plan mapping

The card follows `SubscriptionDisplay::Status` in
`codex-rs/tui/src/subscription.rs` (checked against upstream main on 2026-10-02).
[openai/codex commit fe50d01](https://github.com/openai/codex/commit/fe50d010e203a9b8dda2c7737d7d8e4a80e6ab44)
centralized the TUI labels while preserving deliberate status/analytics differences.
Do not copy `KnownPlan::display_name()` from `codex-rs/protocol/src/auth.rs`
or use `SubscriptionDisplay::Analytics` for the card.

| Raw plan value | `/status` / card label |
| --- | --- |
| `free` | Free |
| `go` | Go |
| `plus` | Plus |
| `prolite` | Pro 100 |
| `pro` | Pro 200 |
| `promax` | Pro 500 |
| `team`, `self_serve_business_usage_based` | Business |
| `business` | Enterprise |
| `self_serve_business_prolite` | Business Premium |
| `ent26`, `enterprise_cbp_usage_based`, `enterprise`, `hc` | Enterprise |
| `enterprise_cbp_automation` | Enterprise (Automation) |
| `edu`, `education` | Edu |
| `edu_plus` | Edu Plus |
| `edu_pro` | Edu Pro |

`hc` and `education` follow the aliases in `PlanType::from_raw_value()` in
`codex-rs/protocol/src/auth.rs`. Keep case-insensitive lookup and whitespace trimming.
Do not relabel `prolite`/`pro`/`promax` as community nicknames such as “5x” or “20x”.
Unknown future values keep the existing fallback: trim whitespace, replace
underscores with spaces, and title-case. Do not collapse them to `Unknown` or
guess a commercial tier. Keep `plan_name()`, its tests, and the README table in sync.

## Verified protocol fields

The integration understands token-count transcript fields in the historical
snake_case shape:

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
    "credits": {
      "hasCredits": true,
      "unlimited": false,
      "balance": "1250.50"
    },
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

2. Test account-only refresh independently:

   ```bash
   python3 ~/.codex/runcat-neo-hook.py --refresh
   python3 -m json.tool ~/.codex/runcat-usage.json
   ```

3. Inspect the latest transcript separately if needed.

4. Determine:
   - whether the main quota is still in `primary`/`secondary`,
   - whether the duration changed,
   - whether `usedPercent` is still “used” rather than “remaining”,
   - whether reset-credit fields changed,
   - whether plan values changed.

5. Make the smallest schema-specific change.

6. Preserve `~/.codex/runcat-usage.json` unless there is a strong reason not to.

7. Run the unit tests and shell syntax checks.

   ```bash
   python3 -m unittest discover -s tests -v
   sh -n install.sh
   sh -n uninstall.sh
   sh -n scripts/test-latest.sh
   ```

8. Verify both paths:
   - `--refresh` with no transcript,
   - a real Stop-hook run after a Codex turn.

## Account switching invariant

Never allow stale credit balances or reset-credit metadata from account A to be displayed as if it
belongs to account B.

The implementation therefore reads account metadata on every Stop-hook and every
background run and does not fall back to cached account/credit/coupon data.

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
- `codex-rs/protocol/src/auth.rs` (raw plan values and aliases only)
- `codex-rs/tui/src/subscription.rs` (`SubscriptionDisplay::Status` plan labels)
- `codex-rs/app-server-protocol/src/protocol/v2/account.rs`
- `codex-rs/tui/src/status/rate_limits.rs` (credits display semantics)
- `codex-rs/backend-client/src/client/rate_limit_resets.rs`
- `codex-rs/tui/src/chatwidget/reset_credits.rs`

RunCat Neo:

- <https://github.com/runcat-dev/RunCatNeo>
