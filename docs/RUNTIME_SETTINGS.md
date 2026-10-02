# Reinstallation and runtime settings

This change follows base commit `a1d8eda7968a50d56dccfd9e8e2fd4eeca2578ae`.
It does not change quota selection, plan labels, credit semantics, RPC, the
metrics schema, the Stop timeout or the 300-second background cadence.

## Shared preservation rule

For each supported setting, a nonempty explicit environment override wins;
otherwise reuse the recorded installation value; use the documented default
only on a fresh installation or when that field was never recorded. An empty
explicit value is an error, not a request to forget the previous value. To
return to a default, explicitly pass that default path. Keep executable symlink
paths (for example Homebrew bin/opt aliases), rather than resolving into Cellar.

The Python used to bootstrap the installer is not automatically a request to
change the installed runtime Python. Both shell entrypoints and direct manager
invocation follow this rule. The shell passes its bootstrap path as a separate
fallback-only argument, preserving its stable alias on fresh installations
without overriding a recorded runtime. If a saved executable has disappeared, stop before
replacing the installation rather than silently choosing a different executable.
An explicit executable override can repair that situation. Installation checks
the version of the selected runtime, not just the bootstrap interpreter.

The contract covers the named settings below, not arbitrary manual plist/wrapper
edits, polling-policy changes, or preservation of unknown environment variables.
Generated wrappers and LaunchAgents are regenerated from the supported settings.
Changing a path does not move or delete data at the old location. Each setting is
independent; a home-directory override does not reset separately saved paths.
Never source/eval an installed shell wrapper to recover settings.

## Verification

`python3 -B -m unittest discover -s tests -v`

The additional runtime-setting tests execute the real `sh install.sh` and
`sh uninstall.sh` entrypoints, with temporary HOME directories and different
bootstrap/runtime Python aliases. macOS telemetry and launchctl are synthetic
or mocked in these tests; they do not replace a native local check. The tests
cover clean-shell reinstallation, explicit overrides, quoted/non-ASCII paths,
empty overrides, saved-executable removal and repeatability. Existing producer,
privacy and installation rollback tests remain in place.

## Codex settings

| Input | Recorded value | Fresh default |
| --- | --- | --- |
| `CODEX_HOME` | Plist environment; legacy script directory if absent | `~/.codex` |
| `RUNCAT_OUT_FILE` | Plist environment | `<CODEX_HOME>/runcat-usage.json` |
| `CODEX_BIN` | Plist environment | Existing producer discovery |
| `RUNCAT_PYTHON_BIN` | Plist `ProgramArguments[0]` | Bootstrap Python (3.9+) |
| `RUNCAT_RUNTIME_PATH` | Plist environment `PATH` | Current shell PATH |

An ordinary shell PATH change does **not** change a saved runtime PATH. Use
`RUNCAT_RUNTIME_PATH` for an intentional change. The Codex executable setting
must identify an executable file (or a command name resolvable in the invoking
shell), not a command plus arguments.

A plain reinstall also recovers a custom CODEX_HOME. Moving an existing
installation to another CODEX_HOME is deliberately refused; uninstall the old
integration first, retaining its data, then install into the new home. This
avoids abandoning a registered hook in the old home. Inconsistent or malformed
saved configuration is rejected before changing files. A legacy plist with no
EnvironmentVariables recovers its home/Python from ProgramArguments; settings
never recorded there cannot be recovered from a previous shell session.

The generated Stop wrapper and LaunchAgent use the same selected Python, Codex,
output and PATH. `scripts/verify_local.py` additionally compares the wrapper
bytes with the runtime recorded in the plist, then checks both refresh paths.

Examples (paths are illustrative):

```sh
# No need to repeat any previous overrides.
sh install.sh

# Intentionally change only the Python runtime.
RUNCAT_PYTHON_BIN=/opt/homebrew/bin/python3.12 sh install.sh

# Explicitly choose a new metric path; the old metric file is kept.
RUNCAT_OUT_FILE="$HOME/.codex/runcat-usage.json" sh install.sh

python3 -B scripts/verify_local.py
```

Evidence in the preceding implementation: a plain reinstall omitted a saved
RUNCAT_OUT_FILE; install.sh overwrote an explicit RUNCAT_PYTHON_BIN; a clean
shell could not recover a custom CODEX_HOME and reselected Codex/PATH. The new
regressions reproduce these behaviors before the fix and pass after it.

## Installed manual commands

From the repository, `python3 -B scripts/run_installed.py refresh`, `show`, or
`diagnose` reads the saved LaunchAgent and uses its recorded runtime. Ambient
project overrides do not change those commands' target; change settings through
an explicit installation override instead. `show` is a saved-file read, not a
fresh observation. The Python that launches this helper need not be the runtime
Python; queries are dispatched to the recorded executable. No wrapper is sourced
or evaluated to recover settings.
