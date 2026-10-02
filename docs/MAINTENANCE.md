# Maintenance

The [README](../README.md) is the user entrypoint. The
[runtime contract](RUNTIME_SETTINGS.md) defines installation precedence and the
[release audit](RELEASE_AUDIT_20261003.md) records the reviewed scope. The earlier
[stabilization notes](STABILIZATION_20261003.md) explain retained observation
semantics; historical test counts there are not current suite counts.

## Runtime contract

The Stop hook invokes the installed `runcat-neo-hook.sh`; launchd invokes the
same saved Python and producer with `--refresh`. Both carry the recorded
CODEX_HOME/CODEX_BIN/PATH/RUNCAT_OUT_FILE. Neither requires manually editing
`hooks.json` or a plist. Preserve output paths and unrelated hooks. Use a
nonempty explicit override for an intentional change; otherwise retain a saved
setting before applying a fresh-install default. Bootstrap Python is not a new
runtime preference. One LaunchAgent label supports one installation per user.

The runtime is Python 3.9+. The combined delivery helper requires 3.10+ because
it also installs the battery integration. Source-test CI uses the hosted
runner's Python and logs its version; it is not a complete Python-version matrix.

## Verify a change

From this repository:

```sh
python3 -B -m unittest discover -s tests -v
sh -n install.sh
sh -n uninstall.sh
sh -n scripts/test-latest.sh
```

After a native installation:

```sh
python3 -B scripts/verify_local.py
python3 -B scripts/run_installed.py refresh
python3 -B scripts/run_installed.py diagnose
```

The one-shot helpers discover the recorded runtime rather than guessing the
current shell's settings. `run_installed.py show` prints the last snapshot and
is not freshness evidence. `scripts/diagnose.py` without the installed helper
is a source-development command using the caller's environment. For an optional
transcript check, `sh scripts/test-latest.sh` returns the protocol `{}` even if
refresh failed; do not treat that response alone as a health check.

Keep code, the README's requirements/manual commands, and RUNTIME_SETTINGS in
sync. Reinstallation tests must execute `sh install.sh`, not merely call the
Python configuration class. Existing tests use synthetic telemetry and fake
launchctl; real login, GUI launchd, a five-minute cycle and RunCat rendering
require the user's Mac and must be reported separately.

## Data and display decisions

Keep the existing `SubscriptionDisplay::Status` plan mapping, documented
unknown-plan fallback, remaining-quota convention and longest-usable-window
policy. Do not change labels from memory or infer commercial policy from a
window duration. For a real upstream change, obtain a privacy-filtered current
response and compare the relevant primary-source protocol before modifying it.

Credits: unlimited takes precedence; otherwise omit unless hasCredits is true.
Only finite positive balances display numerically (whole credit units, decimal
half-up rounding); other balances with hasCredits true display Available.
Never recover credits/coupons from old transcripts or saved account metadata.

Stop quota can come from the newest usable turn-local observation, but account
identity is not checked against the transcript. Offline refresh preserves the
last snapshot, potentially including a previous account. Do not claim complete
cross-account identity coherence; use a successful account-only refresh after
a switch. These semantics were retained, not silently redesigned.

## Privacy and failures

Never log/persist tokens, raw RPC errors, email/account/user IDs or conversation
contents. Diagnostics use the producer's scalar allowlist, including type checks,
not a recursive copy of backend objects. Use synthetic privacy sentinels in tests.
No runtime directories, snapshots, histories, logs or backups belong in commits.

A telemetry failure preserves the snapshot and its timestamp. Account-only
refresh is nonzero on failure; Stop mode remains nonblocking for Codex and
returns `{}`. Do not add an unbounded retry loop or a second resident process.

Installation backs up its own files and attempts restoration on detected write
or registration failure. Inspect the printed backup and explicit failure status;
do not claim power-loss or SIGKILL atomicity. A failed post-install account check
is separate from registration and does not itself prove the files are corrupt.

## CI dependencies

`actions/checkout` is pinned to a full 40-character upstream commit. The version
comment must match the official tag; read-only permissions and
`persist-credentials: false` are retained. SHA pinning prevents a tag moving the
selected action code, but is not a universal security guarantee. Updates require
checking the official source and reviewing the change. No bot is installed to
create unsolicited dependency-update pull requests.

A further mandatory fix should identify the current revision, an observable
failure or concrete security exposure, and a regression test or equivalent
evidence. Optional style changes and new features are not deployment failures.
