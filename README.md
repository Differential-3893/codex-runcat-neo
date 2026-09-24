# codex-runcat-neo

Unofficial OpenAI Codex usage metrics for [RunCat Neo](https://github.com/runcat-dev/RunCatNeo) on macOS.

It installs a user-level Codex `Stop` hook that writes a RunCat Neo Custom Metrics JSON file after each Codex turn.

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
- **Next Expiry** — the earliest expiry among the available reset credits for which the backend returned details.

Known plan display mappings:

| Codex plan value | Display |
| --- | --- |
| `free` | Free |
| `plus` | Plus |
| `prolite` | Pro Lite |
| `pro` | Pro |
| `business` | Business |

Unknown/future values are displayed without guessing a different commercial tier.

## Requirements

- macOS
- Python 3
- Codex signed in with a ChatGPT account
- Codex CLI available as either:
  - `~/.local/bin/codex`, or
  - `codex` on `PATH`
- RunCat Neo

The quota percentage itself is read from the Codex session transcript. Plan and reset-credit metadata are read through the local Codex app-server using the active Codex login.

## Install

Clone the repository:

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/codex-runcat-neo.git
cd codex-runcat-neo
```

Then run:

```bash
./install.sh
```

The installer:

1. installs `runcat-neo-hook.py` at `~/.codex/runcat-neo-hook.py`,
2. backs up an existing hook script and `~/.codex/hooks.json`,
3. merges a `Stop` command hook into the existing `hooks.json` without overwriting unrelated hooks,
4. preserves the output path `~/.codex/runcat-usage.json`.

On the next Codex CLI launch, review/trust the hook if Codex asks you to do so.

## Add the metric to RunCat Neo

After completing one Codex turn, the hook should create:

```text
~/.codex/runcat-usage.json
```

Add that file as a **Custom Metrics** source in RunCat Neo.

You do not need to re-add it after future hook updates as long as the output path stays the same.

## Manual test

After installation and after at least one Codex session exists:

```bash
./scripts/test-latest.sh
```

A standalone line containing:

```json
{}
```

is normal. It is the hook response returned to Codex.

The command then prints the generated RunCat JSON.

## Safe diagnostic

To inspect the current Codex account/rate-limit shape without printing access tokens, email addresses, user IDs, or account IDs:

```bash
python3 scripts/diagnose.py
```

This is useful after a Codex update if the backend schema or quota policy changes.

## Account switching

The hook queries the currently active Codex account on every run for plan/reset-credit metadata.

That is intentional: it avoids showing a previous account's reset coupons after switching accounts.

After switching Codex accounts, complete one turn on the new account. The next RunCat update should then reflect the new account.

If the account-level query fails temporarily, the hook does **not** reuse old coupon metadata from a previous account. The quota percentage can still be produced from the current transcript when available.

## Quota-window selection

Codex can expose a `primary` and a `secondary` rate-limit window.

This integration selects the **longest finite window** as the main account quota. This favors a weekly/monthly-style allowance over a short burst window when both are present.

Known labels:

- 1 day → `Daily Remaining`
- 7 days → `Weekly Remaining`
- other whole-day windows → e.g. `30d Remaining`
- other whole-hour windows → e.g. `5h Remaining`
- unknown duration → `Quota Remaining`

This is deliberately small and mechanical rather than trying to predict future Codex policy.

## Privacy

The RunCat metrics file contains only display data such as:

```json
{
  "title": "Codex",
  "metrics": [
    {
      "title": "Plan",
      "formattedValue": "Pro"
    },
    {
      "title": "Weekly Remaining",
      "formattedValue": "39%",
      "normalizedValue": 0.39
    },
    {
      "title": "Reset",
      "formattedValue": "9/26 11:50"
    },
    {
      "title": "Reset Coupons",
      "formattedValue": "2"
    },
    {
      "title": "Next Expiry",
      "formattedValue": "10/5 13:19"
    }
  ]
}
```

It does **not** write:

- access tokens,
- refresh tokens,
- email addresses,
- ChatGPT account IDs,
- ChatGPT user IDs,
- conversation contents.

Do not commit files from `~/.codex/` to this repository.

## Uninstall

```bash
./uninstall.sh
```

This removes the installed command hook and `~/.codex/runcat-neo-hook.py`.

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
