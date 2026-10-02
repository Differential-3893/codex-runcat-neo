"""Actual sh entrypoints; temporary HOME, synthetic Codex and fake launchctl."""
from __future__ import annotations
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import unittest

import test_install


class RuntimeSettingsTests(unittest.TestCase):
    run_setup = test_install.InstallTests.run_setup
    registered = test_install.InstallTests.registered

    def setUp(self):
        test_install.InstallTests.setUp(self)
        for key in ('RUNCAT_PYTHON_BIN', 'RUNCAT_RUNTIME_PATH', 'PYTHON_BIN'):
            self.env.pop(key, None)

    def payload(self):
        return plistlib.loads(self.plist.read_bytes())

    def clean_reinstall_env(self):
        env = dict(self.env)
        for key in ('CODEX_HOME', 'CODEX_BIN', 'RUNCAT_OUT_FILE', 'RUNCAT_PYTHON_BIN', 'RUNCAT_RUNTIME_PATH'):
            env.pop(key, None)
        # A different shell Python is available. It must not select the runtime.
        other = self.root / 'other bin'
        other.mkdir(exist_ok=True)
        if not (other / 'python3').exists():
            (other / 'python3').symlink_to(sys.executable)
        env['PATH'] = str(other) + os.pathsep + env['PATH']
        return env

    def assert_files_unchanged(self, before):
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def current_files(self):
        return {p: p.read_bytes() for p in (self.script, self.wrapper, self.hooks, self.plist)}

    def test_custom_output_survives_plain_reinstall(self):
        out = self.home / 'elsewhere & quoted\' 한글' / 'usage.json'
        self.run_setup(env={**self.env, 'RUNCAT_OUT_FILE': str(out)})
        self.run_setup()  # no repeated output override
        self.assertEqual(self.payload()['EnvironmentVariables']['RUNCAT_OUT_FILE'], str(out))

    def test_all_runtime_settings_survive_changed_shell_and_unset_overrides(self):
        out = self.home / 'custom output.json'
        alias = self.bin / 'preferred python'
        alias.symlink_to(sys.executable)
        self.run_setup(env={**self.env, 'RUNCAT_OUT_FILE': str(out), 'RUNCAT_PYTHON_BIN': str(alias)})
        before = self.payload()
        self.assertEqual(before['ProgramArguments'][0], str(alias))
        self.run_setup(env=self.clean_reinstall_env())
        self.assertEqual(self.payload(), before)
        self.assertEqual(len(self.registered()), 1)
        subprocess.run([str(self.wrapper), '--refresh'], check=True,
                       env=self.clean_reinstall_env(), capture_output=True, timeout=8)
        self.assertEqual(json.loads(out.read_text())['metricsBarValue'], '39%')
        self.assertFalse((self.home / '.codex/runcat-usage.json').exists())

    def test_explicit_python_is_respected_by_shell_entrypoint(self):
        alias = self.bin / 'python preferred'
        alias.symlink_to(sys.executable)
        self.run_setup(env={**self.env, 'RUNCAT_PYTHON_BIN': str(alias)})
        self.assertEqual(self.payload()['ProgramArguments'][0], str(alias))

    def test_explicit_override_changes_only_requested_runtime_values(self):
        first = self.home / 'first.json'
        second = self.home / 'second.json'
        self.run_setup(env={**self.env, 'RUNCAT_OUT_FILE': str(first)})
        before = self.payload()
        first.write_text('keep the previous data')
        env = self.clean_reinstall_env()
        env['RUNCAT_OUT_FILE'] = str(second)
        self.run_setup(env=env)
        after = self.payload()
        self.assertEqual(after['ProgramArguments'], before['ProgramArguments'])
        self.assertEqual({k:v for k,v in after['EnvironmentVariables'].items() if k != 'RUNCAT_OUT_FILE'},
                         {k:v for k,v in before['EnvironmentVariables'].items() if k != 'RUNCAT_OUT_FILE'})
        self.assertEqual(after['EnvironmentVariables']['RUNCAT_OUT_FILE'], str(second))
        self.assertEqual(first.read_text(), 'keep the previous data')

    def test_explicit_codex_and_runtime_path_overrides_are_recorded(self):
        self.run_setup()
        alias = self.bin / 'codex alias'
        alias.symlink_to(self.codex)
        env = self.clean_reinstall_env()
        env.update(CODEX_BIN=str(alias), RUNCAT_RUNTIME_PATH=self.env['PATH'])
        self.run_setup(env=env)
        saved = self.payload()['EnvironmentVariables']
        self.assertEqual(saved['CODEX_BIN'], str(alias))
        self.assertEqual(saved['PATH'], self.env['PATH'])

    def test_legacy_plist_without_environment_is_migrated(self):
        self.run_setup()
        payload = self.payload()
        payload.pop('EnvironmentVariables')
        self.plist.write_bytes(plistlib.dumps(payload))
        env = self.clean_reinstall_env()
        env['CODEX_BIN'] = self.codex
        self.run_setup(env=env)
        new = self.payload()
        self.assertEqual(new['ProgramArguments'], payload['ProgramArguments'])
        self.assertEqual(new['EnvironmentVariables']['CODEX_HOME'], str(self.codex_home))
        self.assertEqual(new['EnvironmentVariables']['RUNCAT_OUT_FILE'], str(self.codex_home / 'runcat-usage.json'))

    def test_uninstall_recovers_recorded_custom_home(self):
        self.run_setup()
        snapshot = self.codex_home / 'runcat-usage.json'
        snapshot.write_text('preserve')
        self.run_setup('uninstall.sh', env=self.clean_reinstall_env())
        self.assertFalse(self.plist.exists())
        self.assertFalse(self.wrapper.exists())
        self.assertEqual(json.loads(self.hooks.read_text()), self.unrelated)
        self.assertEqual(snapshot.read_text(), 'preserve')

    def test_missing_saved_codex_stops_instead_of_selecting_another(self):
        self.run_setup()
        p = self.payload()
        p['EnvironmentVariables']['CODEX_BIN'] = str(self.home / 'no-longer-installed-codex')
        self.plist.write_bytes(plistlib.dumps(p))
        before = self.current_files()
        calls = (self.root / 'calls').read_bytes()
        env = self.clean_reinstall_env()
        self.run_setup(expected=1, env=env)
        self.assert_files_unchanged(before)
        self.assertEqual((self.root / 'calls').read_bytes(), calls)

    def test_missing_saved_python_stops_without_replacing_configuration(self):
        self.run_setup()
        p = self.payload()
        p['ProgramArguments'][0] = str(self.home / 'no-longer-installed-python')
        self.plist.write_bytes(plistlib.dumps(p))
        before = self.current_files()
        self.run_setup(expected=1, env=self.clean_reinstall_env())
        self.assert_files_unchanged(before)

    def test_empty_explicit_settings_are_errors_not_silent_resets(self):
        self.run_setup()
        before = self.current_files()
        for key in ('CODEX_HOME', 'CODEX_BIN', 'RUNCAT_OUT_FILE', 'RUNCAT_PYTHON_BIN', 'RUNCAT_RUNTIME_PATH'):
            with self.subTest(key=key):
                self.run_setup(expected=1, env={**self.env, key: ''})
                self.assert_files_unchanged(before)

    def test_wrong_label_and_inconsistent_saved_home_fail_before_changes(self):
        self.run_setup()
        original = self.payload()
        for mutate in ('label', 'home', 'environment', 'command'):
            p = plistlib.loads(plistlib.dumps(original))
            if mutate == 'label': p['Label'] = 'another.job'
            if mutate == 'home': p['EnvironmentVariables']['CODEX_HOME'] = str(self.home / 'other')
            if mutate == 'environment': p['EnvironmentVariables'] = []
            if mutate == 'command': p['ProgramArguments'][1] = '/tmp/not-our-program.py'
            self.plist.write_bytes(plistlib.dumps(p))
            before = self.current_files()
            with self.subTest(mutate=mutate):
                self.run_setup(expected=1)
                self.assert_files_unchanged(before)

    def test_output_cannot_overwrite_hook_or_plist(self):
        self.run_setup()
        before = self.current_files()
        for target in (self.script, self.wrapper, self.hooks, self.plist):
            with self.subTest(target=target):
                self.run_setup(expected=1, env={**self.env, 'RUNCAT_OUT_FILE': str(target)})
                self.assert_files_unchanged(before)

    def test_stored_output_and_wrapper_remain_consistent_in_live_verifier(self):
        output = self.home / 'custom.json'
        self.run_setup(env={**self.env, 'RUNCAT_OUT_FILE': str(output)})
        self.run_setup(env=self.clean_reinstall_env())
        result = subprocess.run([sys.executable, '-B', str(test_install.ROOT / 'scripts/verify_local.py')],
                                env=self.clean_reinstall_env(), capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(output.exists())

    def test_failed_registration_restores_saved_runtime_and_hooks(self):
        self.run_setup(env={**self.env, 'RUNCAT_OUT_FILE': str(self.home / 'old custom.json')})
        before = self.current_files()
        (self.root / 'fail_next').touch()
        self.run_setup(expected=1, env={**self.env, 'RUNCAT_OUT_FILE': str(self.home / 'new custom.json')})
        self.assert_files_unchanged(before)
        self.assertTrue((self.root / 'loaded').exists())

    def test_verifier_rejects_wrapper_with_different_runtime(self):
        self.run_setup()
        self.wrapper.write_text(self.wrapper.read_text().replace('RUNCAT_OUT_FILE=', 'WRONG_OUTPUT='))
        r = subprocess.run([sys.executable, '-B', str(test_install.ROOT / 'scripts/verify_local.py')],
                           env=self.env, capture_output=True, text=True, timeout=8)
        self.assertEqual(r.returncode, 1)
        self.assertIn('does not match', r.stderr)

    def test_saved_python_can_be_explicitly_replaced(self):
        self.run_setup()
        alias = self.bin / 'second Python'
        alias.symlink_to(sys.executable)
        before = self.payload()['EnvironmentVariables']
        self.run_setup(env={**self.clean_reinstall_env(), 'RUNCAT_PYTHON_BIN': str(alias)})
        self.assertEqual(self.payload()['ProgramArguments'][0], str(alias))
        self.assertEqual(self.payload()['EnvironmentVariables'], before)

    def test_explicit_default_path_restores_default_without_deleting_custom_data(self):
        custom = self.home / 'custom output.json'
        self.run_setup(env={**self.env, 'RUNCAT_OUT_FILE': str(custom)})
        custom.write_text('keep')
        default = self.codex_home / 'runcat-usage.json'
        self.run_setup(env={**self.clean_reinstall_env(), 'RUNCAT_OUT_FILE': str(default)})
        self.assertEqual(self.payload()['EnvironmentVariables']['RUNCAT_OUT_FILE'], str(default))
        self.assertEqual(custom.read_text(), 'keep')


    def test_output_cannot_replace_auth_config_or_executables(self):
        self.run_setup()
        credentials = self.codex_home / 'auth.json'
        credentials.write_text('{"synthetic":"not-a-real-token"}')
        settings = self.codex_home / 'config.toml'
        settings.write_text('synthetic = true')
        originals = {p:p.read_bytes() for p in (credentials,settings,Path(self.codex),self.bin/'python3')}
        before = self.current_files()
        for p in originals:
            with self.subTest(path=p):
                self.run_setup(expected=1,env={**self.env,'RUNCAT_OUT_FILE':str(p)})
                self.assert_files_unchanged(before)
                self.assertEqual(p.read_bytes(),originals[p])

    def test_failed_stop_does_not_replace_a_running_installation(self):
        self.run_setup()
        launch = self.root/'launch.py'
        s=launch.read_text().replace('args=sys.argv[1:]',
              'args=sys.argv[1:]\nif args[0]=="bootout": sys.exit(5)')
        launch.write_text(s)
        before = self.current_files()
        r=self.run_setup(expected=1,env={**self.env,'RUNCAT_OUT_FILE':str(self.home/'new.json')})
        self.assertIn('Could not stop',r.stderr)
        self.assertTrue((self.root/'loaded').exists())
        self.assert_files_unchanged(before)

    def test_failed_restoration_is_reported_explicitly(self):
        self.run_setup()
        launch=self.root/'launch.py'
        launch.write_text(launch.read_text().replace('args=sys.argv[1:]',
                         'args=sys.argv[1:]\nif args[0]=="bootstrap": sys.exit(5)'))
        before=self.current_files()
        r=self.run_setup(expected=1)
        self.assertIn('RESTORE INCOMPLETE',r.stderr)
        self.assert_files_unchanged(before)

    def test_documented_manual_helper_uses_real_installed_producer(self):
        output=self.home/'manual custom.json'
        self.run_setup(env={**self.env,'RUNCAT_OUT_FILE':str(output)})
        env={**self.clean_reinstall_env(),'RUNCAT_OUT_FILE':str(self.home/'must-not-write.json')}
        for action in ('refresh','show','diagnose'):
            p=subprocess.run([sys.executable,'-B',str(test_install.ROOT/'scripts/run_installed.py'),action],
                             env=env,capture_output=True,text=True,timeout=25)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            value=json.loads(p.stdout)
            if action!='diagnose':self.assertEqual(value['metricsBarValue'],'39%')
            else:self.assertEqual(value['planType'],'pro')
        self.assertFalse((self.home/'must-not-write.json').exists())


if __name__ == '__main__':
    unittest.main()
