"""Per-invocation Stop verification; synthetic accounts and temporary files only."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import test_install as install_fixture
from test_regressions import ROOT, hook, RESPONSE

# The verifier's sibling import uses the source checkout, never a user's install.
sys.path.insert(0, str(ROOT / 'scripts'))
try:
    spec = importlib.util.spec_from_file_location('fresh_stop_verifier', ROOT / 'scripts/verify_local.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
finally:
    sys.path.pop(0)


def card(stamp=1790969000):
    from datetime import datetime, timezone
    return {'title': 'Codex', 'symbol': 'apple.terminal', 'metricsBarValue': '39%',
            'metrics': [{'title': 'Weekly Remaining', 'formattedValue': '39%', 'normalizedValue': .39}],
            'lastUpdatedDate': datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}


class StrictStopTests(unittest.TestCase):
    def invoke(self, flag, data='{}\n', wrote=False, error=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(hook.sys, 'argv', ['runcat-neo-hook.py', *flag]), \
             mock.patch.object(hook.sys, 'stdin', io.StringIO(data)), \
             mock.patch.object(hook, 'write_snapshot', return_value=wrote, side_effect=error), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = hook.main()
        return result, out.getvalue(), err.getvalue()

    def test_normal_stop_remains_fail_open_but_manual_check_fails_closed(self):
        self.assertEqual(self.invoke([]), (0, '{}\n', ''))
        rc, out, err = self.invoke(['--verify-stop'])
        self.assertEqual((rc, out), (1, '{}\n'))
        self.assertIn('no fresh snapshot', err)

    def test_successful_normal_and_strict_stop_have_same_protocol(self):
        for flags in ([], ['--verify-stop']):
            self.assertEqual(self.invoke(flags, wrote=True), (0, '{}\n', ''))

    def test_write_exception_is_failure_without_leaking_message(self):
        for flags, expected in (([], 0), (['--verify-stop'], 1)):
            rc, out, err = self.invoke(flags, error=OSError('PRIVATE_SENTINEL'))
            self.assertEqual((rc, out), (expected, '{}\n'))
            self.assertNotIn('PRIVATE_SENTINEL', err)

    def test_invalid_stdin_is_failure_only_for_strict_check(self):
        for flags, expected in (([], 0), (['--verify-stop'], 1)):
            rc, out, err = self.invoke(flags, data='not-json-PRIVATE_SENTINEL')
            self.assertEqual((rc, out), (expected, '{}\n'))
            self.assertNotIn('PRIVATE_SENTINEL', err)

    def test_refresh_and_strict_stop_modes_are_mutually_exclusive(self):
        with mock.patch.object(hook.sys, 'argv', ['hook', '--refresh', '--verify-stop']), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            hook.main()
        self.assertEqual(error.exception.code, 2)

    def test_strict_stop_checks_real_atomic_write_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'metric.json'
            with mock.patch.object(hook, 'OUT', out), \
                 mock.patch.object(hook, 'account_data', return_value=hook.safe_account_data(RESPONSE)), \
                 mock.patch.object(hook.sys, 'argv', ['hook', '--verify-stop']), \
                 mock.patch.object(hook.sys, 'stdin', io.StringIO('{}')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(hook.main(), 0)
            previous = out.read_bytes()
            with mock.patch.object(hook, 'OUT', out), \
                 mock.patch.object(hook, 'account_data', return_value=None), \
                 mock.patch.object(hook.sys, 'argv', ['hook', '--verify-stop']), \
                 mock.patch.object(hook.sys, 'stdin', io.StringIO('{}')), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(hook.main(), 1)
            self.assertEqual(out.read_bytes(), previous)


class FreshSnapshotTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="verify ' 한글 ")
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / 'custom usage.json'
        self.now = 1790969000.25
        hook.atomic_write_json(self.out, card())

    def check(self, action, *, stop=True, rc=0, stdout=None, stderr=''):
        def run(*args, **kwargs):
            self.assertEqual(kwargs['input'], '{}\n' if stop else None)
            action()
            return subprocess.CompletedProcess(args[0], rc, '{}\n' if stdout is None and stop else stdout or '', stderr)
        with mock.patch.object(verifier.subprocess, 'run', side_effect=run), \
             mock.patch.object(verifier.time, 'time', return_value=self.now):
            return verifier.fresh_snapshot(['synthetic', '--verify-stop'] if stop else ['synthetic', '--refresh'], {}, self.out, stop=stop)

    def test_same_bytes_in_same_second_pass_after_atomic_replacement(self):
        before = self.out.read_bytes()
        self.assertEqual(self.check(lambda: hook.atomic_write_json(self.out, card())), card())
        self.assertEqual(before, self.out.read_bytes())

    def test_unchanged_recent_snapshot_does_not_pass(self):
        with self.assertRaisesRegex(RuntimeError, 'No atomic snapshot replacement'):
            self.check(lambda: None)

    def test_in_place_rewrite_does_not_pass_as_atomic_replacement(self):
        with self.assertRaisesRegex(RuntimeError, 'No atomic snapshot replacement'):
            self.check(lambda: self.out.write_text(json.dumps(card())))

    def test_atomic_replacement_with_stale_timestamp_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'not fresh for this invocation'):
            self.check(lambda: hook.atomic_write_json(self.out, card(1790968900)))

    def test_future_timestamp_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'not fresh for this invocation'):
            self.check(lambda: hook.atomic_write_json(self.out, card(1790969100)))

    def test_failed_stop_cannot_be_hidden_by_another_writer(self):
        with self.assertRaisesRegex(RuntimeError, 'Stop-hook verification failed'):
            self.check(lambda: hook.atomic_write_json(self.out, card()), rc=1)

    def test_wrong_protocol_or_error_output_fails_even_after_write(self):
        for kwargs in ({'stdout': 'not-json'}, {'stderr': 'PRIVATE_SENTINEL'}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(RuntimeError, 'Stop-hook verification failed') as error:
                self.check(lambda: hook.atomic_write_json(self.out, card()), **kwargs)
            self.assertNotIn('PRIVATE_SENTINEL', str(error.exception))

    def test_missing_output_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, 'No snapshot was created'):
            self.check(lambda: self.out.unlink())

    def test_first_write_without_previous_snapshot_passes(self):
        self.out.unlink()
        self.assertEqual(self.check(lambda: hook.atomic_write_json(self.out, card())), card())

    def test_invalid_quota_metric_fails(self):
        for normalized in (True, None, -1, 1.1, '0.39'):
            bad = card()
            bad['metrics'][0]['normalizedValue'] = normalized
            with self.subTest(normalized=normalized), self.assertRaisesRegex(RuntimeError, 'No valid quota'):
                self.check(lambda: hook.atomic_write_json(self.out, bad))

    def test_output_symlink_is_not_followed(self):
        target = self.out.with_name('keep.json')
        target.write_text('untouched')
        self.out.unlink()
        self.out.symlink_to(target)
        with self.assertRaises(OSError):
            self.check(lambda: self.fail('must not invoke with a symlink output'))
        self.assertEqual(target.read_text(), 'untouched')

    def test_account_refresh_also_requires_atomic_freshness(self):
        self.assertEqual(self.check(lambda: hook.atomic_write_json(self.out, card()), stop=False), card())
        with self.assertRaisesRegex(RuntimeError, 'No atomic'):
            self.check(lambda: None, stop=False)


class InstalledStopTests(unittest.TestCase):
    # Reuse the synthetic install fixture without importing its TestCase class
    # into this module (which would duplicate the existing test count).
    setUp = install_fixture.InstallTests.setUp
    run_setup = install_fixture.InstallTests.run_setup

    def sequential_server(self, *, second='error', external_write=False):
        source = self.root / 'fake-server.py'
        source.write_text('''import json, os, sys, time
from pathlib import Path
from datetime import datetime, timezone
response = ''' + repr(RESPONSE) + '''
second = ''' + repr(second) + '''
external_write = ''' + repr(external_write) + '''
root = Path(os.environ['TEST_STATE'])
for line in sys.stdin:
    request = json.loads(line)
    if request.get('method') == 'initialize':
        print(json.dumps({'id':'init','result':{}}), flush=True)
    elif request.get('method') == 'account/rateLimits/read':
        countfile=root/'query-count'
        n=int(countfile.read_text())+1 if countfile.exists() else 1
        countfile.write_text(str(n))
        if n == 1 or second == 'success':
            print(json.dumps({'id':'usage','result':response}), flush=True)
        else:
            if external_write:
                out=Path(os.environ['RUNCAT_OUT_FILE'])
                value=json.loads(out.read_text())
                value['lastUpdatedDate']=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                temporary=out.with_name('synthetic-concurrent-write.json')
                temporary.write_text(json.dumps(value))
                os.replace(temporary,out)
            reply={'id':'usage','error':{'message':'PRIVATE_SENTINEL'}} if second=='error' else {'id':'usage','result':{'rateLimits':{}}}
            print(json.dumps(reply),flush=True)
        time.sleep(10)
''', encoding='utf-8')

    def verify(self):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/verify_local.py')],
                              env=self.env, capture_output=True, text=True, timeout=15)

    def test_successful_account_refresh_then_failed_stop_is_rejected(self):
        self.sequential_server()
        self.run_setup()
        result = self.verify()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('PASS: live account refresh', result.stdout)
        self.assertIn('Stop-hook verification failed', result.stderr)
        self.assertNotIn('LOCAL CHECK PASSED', result.stdout)
        self.assertNotIn('PRIVATE_SENTINEL', result.stdout + result.stderr)
        self.assertEqual((self.root/'query-count').read_text(), '2')
        self.assertEqual(json.loads((self.codex_home/'runcat-usage.json').read_text())['metricsBarValue'], '39%')

    def test_stop_with_empty_quota_is_rejected_after_direct_refresh(self):
        self.sequential_server(second='empty')
        self.run_setup()
        result = self.verify()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn('LOCAL CHECK PASSED', result.stdout)

    def test_other_writer_during_failed_stop_does_not_make_verifier_pass(self):
        self.sequential_server(external_write=True)
        self.run_setup()
        result = self.verify()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('Stop-hook verification failed', result.stderr)

    def test_successful_installed_stop_keeps_settings_and_hooks_unchanged(self):
        self.sequential_server(second='success')
        output = self.home/'custom output 한글.json'
        self.run_setup(env={**self.env, 'RUNCAT_OUT_FILE':str(output)})
        targets = [self.plist, self.hooks, self.wrapper]
        before = {p:p.read_bytes() for p in targets}
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('manual Stop wrote a fresh metric snapshot', result.stdout)
        self.assertIn('LOCAL CHECK PASSED', result.stdout)
        self.assertEqual(before, {p:p.read_bytes() for p in targets})
        self.assertNotIn('--verify-stop', self.hooks.read_text())
        self.assertEqual(json.loads(output.read_text())['metricsBarValue'], '39%')
