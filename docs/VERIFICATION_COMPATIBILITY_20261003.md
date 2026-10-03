# Stop-write verification and rate-limit compatibility — 2026-10-03

Base: `4a6b1ba06c4c6a964d894b81c5855ac85d0fb767` in
`Differential-3893/codex-runcat-neo`. This is a follow-up to the already installed
[consolidated release](RELEASE_AUDIT_20261003.md), not a replacement battery
release. Installation settings, launchers, the CI workflow, card layout,
account query count per invocation and 300-second schedule are unchanged.

## A successful Stop response is not a successful write

The former verifier refreshed the account directly, then called the Stop hook
and checked only exit 0, stdout `{}` and empty stderr. If the second account
query failed without a usable transcript, `write_snapshot` returned False while
normal Stop still returned `{}`/0. The fresh file from the first query made the
verifier's earlier freshness check insufficient. This is reproduced using a
synthetic server that succeeds once and fails on its next query.

Normal Codex Stop remains fail-open: the registered command has no new flags,
and quota refresh failure must not fail a Codex turn. The installed producer
now also accepts **`--verify-stop`**, exclusively for an opt-in manual check.
It follows the same JSON-input, transcript/account selection and atomic-write
path; only its failure exit policy changes. It returns nonzero when that
invocation did not successfully write a snapshot. Successful output is still
`{}`. No receipt file, synthetic account value, new metric field, extra account
request or persistent verification state is introduced.

`python3 -B scripts/verify_local.py` checks the installed source hash, saved
wrapper/environment, registration and exactly one ordinary Stop handler as
before. Both the direct `--refresh` and manual `--verify-stop` calls must now:

1. report success for that invocation (strict status for the manual Stop call),
2. create an atomically replaced regular snapshot at the saved output path,
3. produce a valid quota card with a timestamp within that invocation.

The verifier keeps the previous snapshot descriptor open until the comparison
is complete, so its inode cannot be recycled. Identical contents and identical
second-resolution timestamps are valid when a real atomic replacement occurred;
no arbitrary sleep or requirement that quota numbers change is used. Symlinks
and special files are not followed and snapshot reads are bounded to 1 MiB.
The verifier never deletes/renames the user's current snapshot to simulate a
failure or stops launchd to test it.

A separate writer can replace the output during verification, but cannot turn
this invocation's nonzero strict Stop result into success. A newer concurrent
snapshot may be the one read after a successful invocation. This is not proof
of unique global writer ordering. The test is a manual execution of the
installed Stop path, not observation of a real Codex turn, hook trust approval,
a five-minute timer cycle or the RunCat UI. A transient network/login failure
fails verification; it does not prove the installed files need another patch.

## Single-bucket display in a multi-bucket response

Pinned primary protocol source:
[GetAccountRateLimitsResponse.ts at c5d242fa](https://github.com/openai/codex/blob/c5d242fa7907bff1b7a7e26e95febc548c0a6963/codex-rs/app-server-protocol/schema/typescript/v2/GetAccountRateLimitsResponse.ts)
(checked 2026-10-03), and the [official app-server reference](https://developers.openai.com/codex/app-server/).

The protocol retains `rateLimits` as its backward-compatible **single-bucket**
view. `rateLimitsByLimitId` is additional, optional multi-bucket information.
This integration intentionally continues to use the server's `rateLimits`
view for its one account card. The longest-usable-window rule applies only to
primary/secondary windows within that view, not across unrelated buckets.
The map is not a substitute source for missing plan, quota or credit fields.
A map-only/malformed required single view produces no account snapshot and
preserves the previous file/timestamp; it is not guessed to mean zero usage,
100% remaining, unlimited access or a license to pick an arbitrary bucket.

The fixture `tests/fixtures/rate_limits_compat.json` uses the public field names
but **entirely synthetic values**. It is not a capture of a real account or a
copy of upstream implementation code. Tests cover absent/null/extra/malformed
maps, bucket order, conflicting values, a longer unrelated window, missing
single-view fields, selected credit/plan fields, top-level reset credits and
ignored account identities/banners. They exercise output generation and
preservation of a previous snapshot, not just dictionary selection.

`ordinaryUsageAllowed` is a distinct backend authorization signal. The card
continues to display measured quota; a positive remaining percentage is not a
guarantee that the backend permits a particular model or request. This is not
a complete multi-model quota dashboard. No model-to-bucket mapping is inferred
from labels. Existing Stop transcript preference and account-switch limitations
remain as stated in the README; this patch does not claim identity validation.

## Verification boundaries

Source tests run offline with synthetic telemetry, temporary HOME directories
and fake launchctl on all operating systems, including macOS CI. Additional
regressions exercise an installed wrapper and a real child process: first-read
success/second-read failure, no usable second quota, an unrelated output write
during failure, and successful custom-path execution with unchanged settings.
The distribution records actual test counts and logs separately. Finite tests
and a green CI result are not universal defect-freedom guarantees.
