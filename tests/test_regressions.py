"""Synthetic-only regressions. No real Codex login, network or launchd access."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import select
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("regression_hook", ROOT / "runcat-neo-hook.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
PRIVATE = "SYNTHETIC_PRIVATE_SENTINEL"
QUOTA = {"usedPercent": 61, "windowDurationMins": 10080, "resetsAt": 1790391033}
ACCOUNT = {"planType": "pro", "primary": QUOTA}
RESPONSE = {"rateLimits": {**ACCOUNT, "credits": {"hasCredits": True, "balance": "1250.50"}}}


@contextlib.contextmanager
def child(code):
    proc = subprocess.Popen([sys.executable, "-u", "-c", code],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=0)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=3)
        proc.stdin.close()
        proc.stdout.close()


def ready(proc):
    # Exclude interpreter startup from the deadline-under-test.
    if not select.select([proc.stdout], [], [], 5)[0]:
        raise AssertionError("Synthetic child failed to start")


def fake_codex(directory, response=RESPONSE, mode="normal"):
    source = Path(directory) / "fake-server.py"
    source.write_text('''import json, os, signal, sys, time
response = ''' + repr(response) + '''
mode = ''' + repr(mode) + '''
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
for line in sys.stdin:
    request = json.loads(line)
    if request.get("method") == "initialize":
        os.write(1, b'{"method":"notice"}\\n{"id":"init","result":{}}\\n')
    elif request.get("method") == "account/rateLimits/read":
        if mode == "timeout":
            os.write(1, b'{"id":"usage","result":')
            time.sleep(10)
        elif mode == "eof":
            sys.exit(0)
        elif mode == "error":
            print(json.dumps({"id":"usage","error":{"message":"SYNTHETIC_PRIVATE_SENTINEL"}}), flush=True)
        else:
            wire = json.dumps({"id":"usage","result":response}).encode() + b'\\n'
            os.write(1, b'{"method":"notice"}\\n' + wire[:17])
            time.sleep(0.02)
            os.write(1, wire[17:])
        time.sleep(10)
''', encoding="utf-8")
    path = Path(directory) / "codex test executable"
    path.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -u {shlex.quote(str(source))} \"$@\"\n")
    path.chmod(0o755)
    return str(path)


class PipeTests(unittest.TestCase):
    def test_coalesced_notification_and_response(self):
        wire = b'{"method":"notice"}\n{"id":"usage","result":{"ok":true}}\n'
        with child(f"import os,time; os.write(1,{wire!r}); time.sleep(5)") as proc:
            ready(proc)
            self.assertEqual(hook.read_rpc_response(proc, "usage", time.monotonic() + 0.3), {"ok": True})

    def test_partial_line_deadline_is_enforced(self):
        code = 'import os,time; os.write(1,b\'{"id":"usage","result":\'); time.sleep(0.8); os.write(1,b\'{"ok":true}}\\n\'); time.sleep(5)'
        with child(code) as proc:
            ready(proc)
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                hook.read_rpc_response(proc, "usage", start + 0.15)
            self.assertLess(time.monotonic() - start, 0.6)

    def test_split_utf8_response(self):
        wire = json.dumps({"id": "usage", "result": {"ok": "한글"}}, ensure_ascii=False).encode() + b"\n"
        split = wire.index("한".encode()) + 1
        with child(f"import os,time; os.write(1,{wire[:split]!r}); time.sleep(0.03); os.write(1,{wire[split:]!r}); time.sleep(5)") as proc:
            self.assertEqual(hook.read_rpc_response(proc, "usage", time.monotonic() + 3), {"ok": "한글"})

    def test_shared_buffer_preserves_next_response(self):
        wire = b'{"id":"init","result":{"a":1}}\n{"id":"usage","result":{"b":2}}\n'
        with child(f"import os,time; os.write(1,{wire!r}); time.sleep(5)") as proc:
            ready(proc)
            pending = bytearray()
            deadline = time.monotonic() + 1
            self.assertEqual(hook.read_rpc_response(proc, "init", deadline, pending), {"a": 1})
            self.assertEqual(hook.read_rpc_response(proc, "usage", deadline, pending), {"b": 2})

    def test_ignores_nonobjects_and_invalid_records(self):
        wire = b'[]\nnull\n"str"\nnot-json\n\xff\n{"id":"other","result":{}}\n{"id":"usage","result":{}}\n'
        with child(f"import os,time; os.write(1,{wire!r}); time.sleep(5)") as proc:
            self.assertEqual(hook.read_rpc_response(proc, "usage", time.monotonic() + 3), {})

    def test_eof_before_newline(self):
        with child('import os; os.write(1,b\'{"id":"usage"\')') as proc:
            with self.assertRaises(RuntimeError):
                hook.read_rpc_response(proc, "usage", time.monotonic() + 3)

    def test_rpc_errors_do_not_echo_private_data(self):
        wire = json.dumps({"id": "usage", "error": {"message": PRIVATE}}).encode() + b"\n"
        with child(f"import os,time; os.write(1,{wire!r}); time.sleep(5)") as proc:
            with self.assertRaises(RuntimeError) as caught:
                hook.read_rpc_response(proc, "usage", time.monotonic() + 3)
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_oversized_line_is_bounded(self):
        with child('import os,time; os.write(1,b"x"*5000); time.sleep(5)') as proc:
            with mock.patch.object(hook, "MAX_RPC_LINE_BYTES", 1024), self.assertRaises(RuntimeError):
                hook.read_rpc_response(proc, "usage", time.monotonic() + 3)

    def test_fetch_closes_pipes_and_reaps_process_all_paths(self):
        original = subprocess.Popen
        for mode in ("normal", "error", "eof", "timeout", "ignore_term"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                executable = fake_codex(tmp, mode=mode)
                processes = []
                def start(*args, **kwargs):
                    proc = original(*args, **kwargs)
                    processes.append(proc)
                    return proc
                with mock.patch.object(hook, "find_codex", return_value=executable), \
                     mock.patch.object(hook.subprocess, "Popen", side_effect=start), \
                     mock.patch.object(hook, "ACCOUNT_RPC_TIMEOUT_SECONDS", 0.8):
                    if mode in ("normal", "ignore_term"):
                        self.assertEqual(hook.fetch_account_data()["primary"], QUOTA)
                    else:
                        with self.assertRaises((RuntimeError, TimeoutError)):
                            hook.fetch_account_data()
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())
                self.assertTrue(processes[0].stdin.closed)
                self.assertTrue(processes[0].stdout.closed)


class FallbackTests(unittest.TestCase):
    def test_bad_transcript_does_not_block_account_fallback(self):
        records = [b'[]\n', b'{"payload": []}\n', b'{"payload": "bad"}\n',
                   b'\xff\n', b'{"payload":{"type":"token_count","rate_limits":{"primary":{}}}}\n',
                   b'{"payload":{"type":"token_count","rate_limits":[]}}\n']
        for content in records:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as tmp:
                transcript, out = Path(tmp)/"session.jsonl", Path(tmp)/"metric.json"
                transcript.write_bytes(content)
                with mock.patch.object(hook, "OUT", out), mock.patch.object(hook, "account_data", return_value=ACCOUNT) as account:
                    self.assertTrue(hook.write_snapshot({"transcript_path": str(transcript)}))
                self.assertEqual(account.call_count, 1)
                self.assertEqual(json.loads(out.read_text())["metricsBarValue"], "39%")

    def test_bad_latest_token_uses_account_not_older_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp)/"session.jsonl"
            events = [{"payload": {"type": "token_count", "rate_limits": {"primary": {"used_percent": 1}}}},
                      {"payload": {"type": "token_count", "rate_limits": {"primary": {}}}}]
            transcript.write_text("\n".join(map(json.dumps, events)))
            self.assertEqual(hook.latest_token_count(str(transcript)), events[-1]["payload"])

    def test_transcript_tail_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp)/"large.jsonl"
            record = {"payload": {"type": "token_count", "rate_limits": {"primary": {"used_percent": 61}}}}
            transcript.write_bytes(b"x" * 10000 + b"\n" + json.dumps(record).encode() + b"\n")
            with mock.patch.object(hook, "MAX_TRANSCRIPT_BYTES", 512):
                self.assertEqual(hook.latest_token_count(str(transcript)), record["payload"])

    def test_fifo_and_invalid_path_do_not_block(self):
        for path in (None, [], {}, 1, "\0"):
            self.assertIsNone(hook.latest_token_count(path))
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp)/"fifo"
            os.mkfifo(fifo)
            self.assertIsNone(hook.latest_token_count(str(fifo)))

    def test_nonfinite_and_bool_quota_not_published_as_100_percent(self):
        for used in (True, False, float("nan"), float("inf"), -float("inf"), "61", None):
            with self.subTest(used=used), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)/"metric.json"
                out.write_bytes(b'{"unchanged":true}')
                before = out.stat().st_mtime_ns
                with mock.patch.object(hook, "OUT", out), mock.patch.object(hook, "account_data", return_value={"primary": {"usedPercent": used}}):
                    self.assertFalse(hook.refresh_from_account())
                self.assertEqual(out.read_bytes(), b'{"unchanged":true}')
                self.assertEqual(out.stat().st_mtime_ns, before)

    def test_selects_valid_window_not_empty_longest(self):
        self.assertEqual(hook.select_main_quota_window([
            {"windowDurationMins": 20000}, QUOTA]), QUOTA)
        self.assertEqual(hook.select_main_quota_window([
            {"windowDurationMins": float("inf"), "usedPercent": 10}, QUOTA]), QUOTA)

    def test_custom_codex_home_and_output_override(self):
        for override in (None, "other/result.json"):
            with tempfile.TemporaryDirectory() as tmp:
                env = {**os.environ, "HOME": tmp, "CODEX_HOME": str(Path(tmp)/"custom")}
                env.pop("RUNCAT_OUT_FILE", None)
                if override:
                    env["RUNCAT_OUT_FILE"] = str(Path(tmp)/override)
                code = 'import runpy; m=runpy.run_path(' + repr(str(ROOT/"runcat-neo-hook.py")) + '); print(m["OUT"])'
                result = subprocess.check_output([sys.executable,"-B","-c",code], env=env, text=True).strip()
                self.assertEqual(result, env.get("RUNCAT_OUT_FILE", str(Path(tmp)/"custom/runcat-usage.json")))

    def test_atomic_failure_preserves_file_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/"result.json"
            out.write_bytes(b"original")
            with mock.patch.object(hook.os, "replace", side_effect=OSError("synthetic")), self.assertRaises(OSError):
                hook.atomic_write_json(out, {"new": True})
            self.assertEqual(out.read_bytes(), b"original")
            self.assertEqual(list(Path(tmp).iterdir()), [out])

    def test_stop_failure_is_json_and_does_not_echo_exception(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys,"argv",["hook"]), mock.patch.object(sys,"stdin",io.StringIO("{}")), \
             mock.patch.object(hook, "write_snapshot", side_effect=ValueError(PRIVATE)), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(hook.main(), 0)
        self.assertEqual(stdout.getvalue(), "{}\n")
        self.assertNotIn(PRIVATE, stderr.getvalue())

    def test_refresh_failure_has_nonzero_exit_without_private_error(self):
        stderr = io.StringIO()
        with mock.patch.object(sys,"argv",["hook","--refresh"]), \
             mock.patch.object(hook,"refresh_from_account",side_effect=RuntimeError(PRIVATE)), contextlib.redirect_stderr(stderr):
            self.assertEqual(hook.main(), 1)
        self.assertNotIn(PRIVATE, stderr.getvalue())


class DiagnosticTests(unittest.TestCase):
    def test_real_diagnostic_process_filters_nested_metadata(self):
        response = {"rateLimits": {"planType": "pro", "primary": {**QUOTA, "email": PRIVATE, "accessToken": PRIVATE},
                     "secondary": {"usedPercent": {"private": PRIVATE}},
                     "credits": {"hasCredits": True, "balance": "25", "email": PRIVATE}},
                    "rateLimitResetCredits": {"availableCount": 2, "credits": [{"status": "available", "expiresAt": 1791173986,
                     "title": PRIVATE, "description": PRIVATE, "accountId": PRIVATE}]}}
        with tempfile.TemporaryDirectory() as tmp:
            executable = fake_codex(tmp, response)
            env = {**os.environ, "HOME": tmp, "CODEX_HOME": str(Path(tmp)/"custom"), "CODEX_BIN": executable}
            result = subprocess.run([sys.executable,"-B",str(ROOT/"scripts/diagnose.py")], env=env, capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(PRIVATE, result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["primary"], QUOTA)
            self.assertEqual(data["resetCoupons"]["credits"][0], {"status": "available", "expiresAt": 1791173986})

    def test_diagnostic_rpc_error_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = fake_codex(tmp, mode="error")
            env = {**os.environ, "HOME": tmp, "CODEX_HOME": tmp, "CODEX_BIN": executable}
            result = subprocess.run([sys.executable,"-B",str(ROOT/"scripts/diagnose.py")], env=env, capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertNotIn(PRIVATE, result.stderr)


if __name__ == "__main__":
    unittest.main()
