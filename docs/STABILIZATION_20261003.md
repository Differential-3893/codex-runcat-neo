# Stabilization — 2026-10-03

Based on `66ccb6993c1fcb2866ddd31c3696f8bf6a7518e7`.

The card, plan mapping, positive-credit half-up rounding, default output path,
Stop trigger and 300-second launchd cadence are unchanged. No third-party Python
dependencies or new resident process are added.

## Runtime changes

The account query now uses binary pipe reads and a shared line buffer, not
`select()` followed by buffered text `readline()`. Coalesced notifications and
fragmented replies obey the same three-second query deadline. Child processes
are terminated, reaped and their pipes closed on success, error and timeout.

The last token-count record is selected from a bounded 4 MiB transcript tail
with a 0.35-second parsing budget. Non-object records, bad encodings and invalid
payloads are ignored. If that newest token-count record has no usable window,
the producer uses the account response rather than an older transcript record.
If neither source has a finite numeric quota, the snapshot and its timestamp
are left unchanged. Boolean values are not measurements. A non-positive or
nonfinite duration is not considered a finite quota window.

Diagnostic output uses the same scalar-field projection as the producer.
Nested backend objects, coupon titles/descriptions and raw RPC/exception error
messages are not printed. Tests use synthetic private sentinels, not real data.

## Installation and support

Use macOS and Python 3.9 or newer. Run `sh install.sh` from the source directory.
The installed producer remains `runcat-neo-hook.py`. A small adjacent
`runcat-neo-hook.sh` launcher pins the Python/Codex executable paths and the same
CODEX_HOME/PATH used by the LaunchAgent. Reopen Codex and approve the changed hook
command if prompted. There is still just one Stop registration, not two.

`CODEX_HOME` defaults to `~/.codex`. Its absolute path is passed to the installed
hook, app-server and LaunchAgent. Optional `CODEX_BIN` and `RUNCAT_OUT_FILE` are
captured consistently at install time. Use the same CODEX_HOME for uninstalling.
Only one installation of this LaunchAgent label per macOS user is supported;
installing into another home while that label targets an existing home is refused.

The installer validates hooks.json before replacing files, removes only this
integration's previous commands, and backs up existing targets into a private
`runcat-backup-*` directory under CODEX_HOME. An installation/registration failure
restores previous files and attempts to reload the previous job if it was loaded.
Unrelated hooks and settings are preserved. The metric snapshot, logs and backup
files are not deleted on uninstall.

The initial refresh is triggered by launchd's RunAtLoad rather than an additional
redundant account query. `--refresh` now exits with status 1 when no new snapshot
was written; Stop-hook failures still return `{}` with status 0 so a metric cannot
fail the Codex turn. Neither failure overwrites the old snapshot.

Run `python3 scripts/verify_local.py` after installing. This checks the installed
source, LaunchAgent registration, a live account refresh and a manual Stop call.
It does not claim to observe a real Codex turn, wait for the timer, or inspect the
RunCat UI. `sh scripts/test-latest.sh` remains an optional manual transcript test.

## Deliberately unchanged observation semantics

Stop still prefers a usable turn-local transcript quota and reads plan, credits
and coupons from the current account query. A transcript's account identity is
not independently verified. Therefore cross-account coherence of every card row
is not guaranteed during a switch; do not infer identity from plan labels.
An offline refresh preserves the last snapshot, which can be from before a switch.
Run an account-only refresh after switching to establish a coherent new snapshot.
No account identifiers or cached credit/coupon fallbacks have been introduced.

## Verification boundary

Run `python3 -B -m unittest discover -s tests -v` and individual `sh -n` checks.
CI runs on Linux and macOS; installer tests always use fake launchctl and a
synthetic HOME, including on macOS. They are not evidence of real GUI launchd
registration. Baseline stream and fallback failures were reproduced on Linux;
the patched regression suite was executed on Linux. Live Mac/Codex/RunCat checks
must be performed on the installation machine using the supplied verifier.

A new mandatory fix requires a reproducible failure and user impact, not a style
preference or a new feature idea. This patch does not claim universal defect freedom.
