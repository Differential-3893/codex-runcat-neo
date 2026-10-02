# codex-runcat-neo

Unofficial OpenAI Codex usage metrics for [RunCat Neo](https://github.com/runcat-dev/RunCatNeo) on macOS.
This project is not affiliated with or endorsed by OpenAI or RunCat.

```text
Codex turn completes -> Stop hook -> installed launcher -> refresh
                                                        +
launchd every 5 min  -> saved Python + producer -> account refresh
                                                        |
                                                        v
                                    ~/.codex/runcat-usage.json (default)
                                                        |
                                                        v
                                                    RunCat Neo
```

The Stop hook can use the completed turn's transcript for a recent quota
observation. The five-minute account-only refresh needs no transcript and keeps
working while Codex is idle. Neither path is a continuous real-time monitor.

## What it shows

```text
Codex

Plan: Pro 200
Weekly Remaining: 39%
[████████░░░░░░░░░░░░]

Reset: 9/26 11:50
Credits Remaining: 1,251
Reset Coupons: 2
Next Expiry: 10/5 13:19
```

This is an illustrative card, not live account data. It shows the account plan,
remaining quota, the backend reset time in the Mac's local timezone, credit
balance when supplied, and the available reset-coupon count/earliest expiry.
`apple.terminal` is a generic SF Symbol, not an official Codex logo.

Credits are Codex credit units, not dollars or percentages. `unlimited: true`
shows `Unlimited` even when `hasCredits` is false. Otherwise the row is omitted
unless `hasCredits` is true. Finite positive balances are rounded half up to
whole credits (`1250.50` becomes `1,251`; `0.001` becomes `0`). A hidden,
invalid, zero or negative balance with `hasCredits: true` displays `Available`.
Credits and coupons are read from the current account response, not recovered
from transcript or cached account metadata.

The plan labels below retain the mapping checked against Codex
`SubscriptionDisplay::Status` on 2026-10-02, rather than analytics labels:

| Codex plan value | Display |
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

Unknown plan values are trimmed, underscores become spaces, and the result is
title-cased without guessing a commercial tier. This is not a promise that
future upstream policies or plan names will remain unchanged.

## Requirements

- macOS and RunCat Neo with Custom Metrics support.
- Python **3.9+** for this repository's installer, helpers and producer; no
  third-party Python packages. Use a supported Python release for regular use.
- A Codex CLI compatible with `codex app-server --listen stdio://`, signed in
  with a ChatGPT account. On a fresh install, set `CODEX_BIN`, or have Codex at
  `~/.local/bin/codex` or on `PATH`.

The separate combined two-repository delivery helper requires Python 3.10+
(the battery integration's minimum). Its bootstrap Python is not automatically
selected as this integration's saved runtime Python.

## Install or update

```sh
git clone https://github.com/Differential-3893/codex-runcat-neo.git
cd codex-runcat-neo
sh install.sh
python3 -B scripts/verify_local.py
```

For an existing clone, update its source first without overwriting uncommitted
work, then run the last two commands. No `sudo` is needed.

The installer validates the saved configuration and executable paths before
replacing files. It backs up its targets in a private `runcat-backup-*` directory
under `CODEX_HOME`, installs `runcat-neo-hook.py` and the adjacent
`runcat-neo-hook.sh` launcher, and merges exactly one owned Stop command into
`hooks.json`. Unrelated hooks are preserved. It registers
`~/Library/LaunchAgents/dev.runcat.codex-usage.plist` with a 300-second interval.
`RunAtLoad` triggers the initial refresh; there is no redundant installer query.

The launcher and LaunchAgent use the same saved Python, Codex executable,
`CODEX_HOME`, output file and runtime `PATH`. Reopen Codex and approve the changed
hook command if prompted. A successful install registration is distinct from a
successful live account read: `scripts/verify_local.py` checks both refresh paths
and prints `LOCAL CHECK PASSED` when its checks succeed. An offline account check
may fail without undoing a successfully registered installation; the old metric
snapshot is kept. The verifier does not observe a real Codex turn, a full timer
cycle, or the RunCat user interface.

On detected file/registration failure the installer restores previous target
files and attempts to restore the previous job. Keep the printed backup if an
operation or restoration fails; this is not crash/power-loss atomicity.

## Reinstalling without losing custom settings

Selection order is **explicit nonempty override → recorded installation value →
fresh default**. Empty overrides are errors, not implicit resets. A different
Python on today's shell `PATH` does not replace a saved runtime Python.

A plain `sh install.sh` reuses the recorded `CODEX_HOME`, `RUNCAT_OUT_FILE`,
`CODEX_BIN`, Python and runtime `PATH`. Use `RUNCAT_PYTHON_BIN` or
`RUNCAT_RUNTIME_PATH` for deliberate runtime changes. A missing saved executable
is reported instead of silently selecting a different one.

```sh
# Example only: select an existing interpreter explicitly.
RUNCAT_PYTHON_BIN=/opt/homebrew/bin/python3.12 sh install.sh
```

Changing to a different `CODEX_HOME` while the old installation exists is refused;
uninstall this integration first, then install into the new home. Changing an
output path does not move/delete the old snapshot. See
[the complete runtime-setting contract](docs/RUNTIME_SETTINGS.md) for all
supported overrides and legacy migration limits. Arbitrary manual plist or
wrapper edits are not a supported settings interface.

## Add the metric to RunCat Neo

The default source is `~/.codex/runcat-usage.json`. Add it in RunCat Neo's Custom
Metrics settings. With a custom output path, use the path printed by the installer.
Do not re-add a source whose path has not changed.

## Manual refresh, display and diagnosis

Run these from this repository. They read the installed LaunchAgent, including a
custom home/output path, and use its **saved Python and environment**:

```sh
python3 -B scripts/run_installed.py refresh
python3 -B scripts/run_installed.py show
python3 -B scripts/run_installed.py diagnose
```

`refresh` queries the current account and prints a newly written snapshot; it
returns a nonzero status if it cannot verify a fresh result. `show` only prints
the last saved snapshot, which may be stale; it makes no account request.
`diagnose` prints only the installed producer's allowlisted account metadata and
does not write a snapshot. Raw responses, token values and exception details are
not printed on command failure. These helpers are one-shot commands, not new
background jobs. Calling the producer with an arbitrary shell `python3` directly
can bypass the installed runtime settings, so it is not the recommended path.

For source-development diagnostics before installation,
`python3 -B scripts/diagnose.py` deliberately uses the source tree and the
invoking shell's Python/environment; it is not an installed-runtime check.

After at least one session, an optional transcript-based Stop test is:

```sh
sh scripts/test-latest.sh
```

It discovers the saved home/output path and calls the installed launcher. A
standalone `{}` is the hook protocol response, **not proof of a successful
snapshot update**. The hook must not fail a Codex turn when telemetry fails.
Use `run_installed.py refresh` or `verify_local.py` for a strict live check.

## Quota selection and failure behavior

Only usable finite numeric quota windows participate. Among windows with a
finite positive duration, the longest is selected; a usable unknown-duration
window is a fallback. Seven days is `Weekly Remaining`, one day is
`Daily Remaining`, other whole-day/hour windows are e.g. `30d Remaining` or
`5h Remaining`, and other durations are `Quota Remaining`. The card reports
remaining quota, not used quota.

The Stop hook first considers the newest token-count record in a bounded
transcript tail; unusable transcript data falls back to the current account
response rather than an older record. An account-only failure or absence of
usable quota preserves the existing snapshot **and its timestamp**. `--refresh`
then exits with status 1. Stop-hook mode returns `{}` with status 0 so a metric
failure cannot fail the Codex turn.

## Account switching

Plan, credits and coupons are obtained from the current account query each run.
A Stop transcript is not independently matched to account identity, so a card
can combine a transcript quota with current account metadata during a switch.
An offline refresh may leave the entire pre-switch snapshot visible. There is
no stale credit/coupon cache fallback, but this is not an account-identity
coherence guarantee. After switching, a successful account-only
`python3 -B scripts/run_installed.py refresh` establishes a new account snapshot.
No account identifiers are persisted for reconciliation.

## Background job, privacy and removal

```sh
launchctl print "gui/$(id -u)/dev.runcat.codex-usage"
```

The producer uses the signed-in local Codex app-server, which queries the account;
it is not an offline-only integration. The RunCat snapshot contains display data,
not tokens, email addresses, account/user IDs or conversation contents. Do not
commit files from `~/.codex` or another runtime home, diagnostics, logs or backups.

```sh
sh uninstall.sh
```

This removes the owned Stop command, launcher, producer and LaunchAgent while
keeping unrelated hooks, metric data, logs and backups. The saved custom home is
recovered automatically. See [maintenance notes](docs/MAINTENANCE.md) for
verification, failure handling and supported update scope.

## Tests and CI

```sh
python3 -B -m unittest discover -s tests -v
sh -n install.sh
sh -n uninstall.sh
sh -n scripts/test-latest.sh
```

CI runs the suite on Linux and macOS with read-only repository permission,
SHA-pinned checkout, no persisted checkout credentials, and a bounded job time.
Tests use synthetic account responses and mock/fake launchctl even on macOS;
a green CI run is not proof of a real login, GUI service, timer or RunCat display.
The suite covers pipe framing/deadlines, privacy, fallback, installation/rollback,
real shell reinstallation, installed-runtime commands, and documentation/CI
contracts. [Release audit](docs/RELEASE_AUDIT_20261003.md) records this delivery's
scope; [earlier stabilization notes](docs/STABILIZATION_20261003.md) are historical.

## Upstream references

These are implementation references, not compatibility guarantees:

- [Codex account protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/src/protocol/v2/account.rs)
- [Codex status plan labels](https://github.com/openai/codex/blob/main/codex-rs/tui/src/subscription.rs)
- [Status-label centralization](https://github.com/openai/codex/commit/fe50d010e203a9b8dda2c7737d7d8e4a80e6ab44)
- [Raw plan aliases](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/auth.rs)
- [Credit display semantics](https://github.com/openai/codex/blob/main/codex-rs/tui/src/status/rate_limits.rs)
- [Reset-credit backend](https://github.com/openai/codex/blob/main/codex-rs/backend-client/src/client/rate_limit_resets.rs)
- [Reset-credit interface](https://github.com/openai/codex/blob/main/codex-rs/tui/src/chatwidget/reset_credits.rs)
- [RunCat Neo](https://github.com/runcat-dev/RunCatNeo)

## License

MIT. See [LICENSE](LICENSE).
