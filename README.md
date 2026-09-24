# codex-runcat-neo

Unofficial OpenAI Codex usage metrics for [RunCat Neo](https://github.com/runcat-dev/RunCatNeo) on macOS.

The integration uses two refresh paths:

```text
Codex turn completes -> Stop hook -> immediate refresh
                         +
launchd every 5 min  -> account refresh
                         |
                         v
              ~/.codex/runcat-usage.json
                         |
                         v
                   RunCat Neo
```

This means the card stays useful even when Codex is not currently being used, while a completed Codex turn can still update it immediately.

Example card:

```text
Codex

Plan: Pro
Weekly Remaining: 39%
[████████░░░░░░░░░░░░]

Reset: 9/26 11:50
Reset Coupons: 2
Next Expiry: 10/5 13:19
```

This project is not affiliated with or endorsed by OpenAI or RunCat.

## What it shows

- **Plan** — the current Codex account plan.
- **Remaining quota** — the main account quota shown as *remaining*, not used.
- **Reset** — the backend-provided reset time in your Mac's local timezone.
- **Reset Coupons** — the number of currently available Codex usage-limit reset credits.
- **Next Expiry** — the earliest expiry among available reset credits for which the backend returned details.

Known plan display mappings:

| Codex plan value | Display |
| --- | --- |
| `free` | Free |
| `plus` | Plus |
| `prolite` | Pro Lite |
| `pro` | Pro |
| `business` | Business |

Unknown/future values are displayed without guessing a different commercial tier.

## Refresh behavior

The integration deliberately combines event-driven and scheduled refreshes.

- **After a Codex turn:** the user-level `Stop` hook refreshes the snapshot immediately. The turn transcript is preferred for the quota window because it is the freshest turn-local observation.
- **While Codex is idle:** a macOS LaunchAgent runs `runcat-neo-hook.py --refresh` every **5 minutes**. This queries the currently active Codex account directly through the local Codex app-server, so no transcript or active Codex session is required.

Five minutes is intentionally a background cadence, not a real-time sampling rate. Quota reset/coupon/account metadata changes slowly, while immediate post-usage changes are already covered by the Stop hook.

If a background query temporarily fails, the existing RunCat JSON snapshot is left intact rather than being replaced with guessed or stale cross-account metadata.

## Symbol

The card uses the SF Symbol:

```text
apple.terminal
```

It is an abstract crossed-swirl glyph that fits Codex better than the previous `camera.aperture` symbol while remaining a generic system symbol rather than pretending to be the official Codex logo.

## Requirements

- macOS
- Python 3
- Codex signed in with a ChatGPT account
- Codex CLI available as either:
  - `~/.local/bin/codex`, or
  - `codex` on `PATH`
- RunCat Neo

Account-level quota, plan, reset time, and reset-credit metadata are read through the local Codex app-server using the active Codex login. The Stop hook can additionally use the current session transcript for the freshest quota observation immediately after a turn.

## Install

Clone the repository:

```bash
git clone https://github.com/Differential-3893/codex-runcat-neo.git
cd codex-runcat-neo
```

Then run:

```bash
sh install.sh
```

The installer:

1. installs `runcat-neo-hook.py` at `~/.codex/runcat-neo-hook.py`,
2. backs up an existing hook script and `~/.codex/hooks.json`,
3. merges a `Stop` command hook into the existing `hooks.json` without overwriting unrelated hooks,
4. installs `~/Library/LaunchAgents/dev.runcat.codex-usage.plist`,
5. performs one immediate account refresh,
6. schedules account refreshes every five minutes,
7. preserves the output path `~/.codex/runcat-usage.json`.

On the next Codex CLI launch, review/trust the hook if Codex asks you to do so.

## Add the metric to RunCat Neo

The integration writes:

```text
~/.codex/runcat-usage.json
```

Add that file as a **Custom Metrics** source in RunCat Neo.

You do not need to re-add it after future updates as long as the output path stays the same.

## Manual account refresh

This works without completing a Codex turn:

```bash
python3 ~/.codex/runcat-neo-hook.py --refresh
python3 -m json.tool ~/.codex/runcat-usage.json
```

## Manual Stop-hook test

After installation and after at least one Codex session exists:

```bash
sh scripts/test-latest.sh
```

A standalone line containing:

```json
{}
```

is normal. It is the hook response returned to Codex.

## Safe diagnostic

To inspect the current Codex account/rate-limit shape without printing access tokens, email addresses, user IDs, or account IDs:

```bash
python3 scripts/diagnose.py
```

This is useful after a Codex update if the backend schema or quota policy changes.

## Account switching

Both refresh paths query the currently active Codex account for account-level metadata.

That is intentional: it avoids showing a previous account's reset coupons after switching accounts. A background refresh will normally pick up a switched account within five minutes even if no Codex turn is completed; a completed turn updates it immediately.

The integration does not use stale account/coupon cache fallback.

## Quota-window selection

Codex can expose a `primary` and a `secondary` rate-limit window.

This integration selects the **longest finite window** as the main account quota. This favors a weekly/monthly-style allowance over a short burst window when both are present.

Known labels:

- 1 day → `Daily Remaining`
- 7 days → `Weekly Remaining`
- other whole-day windows → e.g. `30d Remaining`
- other whole-hour windows → e.g. `5h Remaining`
- unknown duration → `Quota Remaining`

This is deliberately mechanical rather than trying to predict future Codex policy.

## Privacy

The RunCat metrics file contains only display data such as plan, remaining quota, reset times, and reset-credit count/expiry.

It does **not** write:

- access tokens,
- refresh tokens,
- email addresses,
- ChatGPT account IDs,
- ChatGPT user IDs,
- conversation contents.

Do not commit files from `~/.codex/` to this repository.

## Background job status

```bash
launchctl print gui/$(id -u)/dev.runcat.codex-usage
```

The LaunchAgent interval is 300 seconds.

## Uninstall

```bash
sh uninstall.sh
```

This removes the Stop-hook registration, background LaunchAgent, and installed hook script.

It keeps `~/.codex/runcat-usage.json` so RunCat does not suddenly lose the source file. Delete that file manually if you want to remove the generated data too.

## After a future Codex update

If the card stops updating or the quota structure changes:

```bash
python3 scripts/diagnose.py
```

Then compare the output against the hook logic.

See [docs/MAINTENANCE.md](docs/MAINTENANCE.md) for the maintenance workflow intended for either a human maintainer or an AI coding assistant.

## Upstream references

The implementation follows public Codex protocol behavior, including:

- account plan types:
  <https://github.com/openai/codex/blob/main/codex-rs/protocol/src/account.rs>
- account/rate-limit protocol types:
  <https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/src/protocol/v2/account.rs>
- account rate-limit/reset-credit backend reads:
  <https://github.com/openai/codex/blob/main/codex-rs/backend-client/src/client/rate_limit_resets.rs>
- Codex reset-credit UI behavior:
  <https://github.com/openai/codex/blob/main/codex-rs/tui/src/chatwidget/reset_credits.rs>
- RunCat Neo:
  <https://github.com/runcat-dev/RunCatNeo>

These are implementation references, not compatibility guarantees.

## License

MIT. See [LICENSE](LICENSE).
