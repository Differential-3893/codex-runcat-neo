"""Install tests use a temporary HOME and fake uname/launchctl on every OS."""
from __future__ import annotations

import json
import os
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_regressions import ROOT, fake_codex


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home with spaces 한글"
        self.home.mkdir()
        self.codex_home = self.home / "custom Codex"
        self.codex_home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin/"python3").symlink_to(sys.executable)
        self.codex = fake_codex(self.root)
        (self.bin/"uname").write_text("#!/bin/sh\nprintf 'Darwin\\n'\n")
        (self.bin/"uname").chmod(0o755)
        launcher = self.bin/"launchctl"
        launcher.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(self.root/"launch.py")) + ' "$@"\n')
        launcher.chmod(0o755)
        (self.root/"launch.py").write_text('''import json,os,sys
from pathlib import Path
root=Path(os.environ["TEST_STATE"])
args=sys.argv[1:]
with (root/"calls").open("a") as f: f.write(json.dumps(args)+"\\n")
loaded=root/"loaded"
if args[0]=="print": sys.exit(0 if loaded.exists() else 113)
if args[0]=="bootout": loaded.unlink(missing_ok=True)
if args[0]=="bootstrap":
    fail=root/"fail_next"
    if fail.exists():
        fail.unlink()
        sys.exit(5)
    loaded.write_text("yes")
''')
        self.env = {**os.environ,"HOME":str(self.home),"CODEX_HOME":str(self.codex_home),
                    "CODEX_BIN":self.codex,"PATH":str(self.bin)+os.pathsep+os.environ.get("PATH",os.defpath),
                    "TEST_STATE":str(self.root),"PYTHONDONTWRITEBYTECODE":"1"}
        self.env.pop("RUNCAT_OUT_FILE",None)
        self.plist = self.home/"Library/LaunchAgents/dev.runcat.codex-usage.plist"
        self.hooks = self.codex_home/"hooks.json"
        self.script = self.codex_home/"runcat-neo-hook.py"
        self.wrapper = self.codex_home/"runcat-neo-hook.sh"
        self.unrelated = {"keep": {"nested": True}, "hooks": {"SessionStart":[{"hooks":[{"type":"command","command":"echo other"}]}],
            "Stop":[{"matcher":"keep","hooks":[{"type":"command","command":"echo keep","timeout":99}]}, {"hooks":[]} ]}}
        self.hooks.write_text(json.dumps(self.unrelated))

    def run_setup(self, filename="install.sh", expected=0, env=None):
        result = subprocess.run(["sh",str(ROOT/filename)], env=env or self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout+result.stderr)
        return result

    def registered(self):
        data=json.loads(self.hooks.read_text())
        return [h for g in data["hooks"].get("Stop",[]) if isinstance(g,dict)
                for h in g.get("hooks",[]) if isinstance(h,dict) and str(self.wrapper) in h.get("command","")]

    def test_install_reinstall_and_uninstall_preserve_unrelated_hooks(self):
        legacy=json.loads(json.dumps(self.unrelated))
        legacy["hooks"]["Stop"].append({"hooks":[{"type":"command","command":str(self.script),"timeout":5}]*2})
        self.hooks.write_text(json.dumps(legacy))
        self.run_setup()
        self.run_setup()
        self.assertEqual(len(self.registered()),1)
        data=plistlib.loads(self.plist.read_bytes())
        self.assertEqual(data["StartInterval"],300)
        self.assertTrue(data["RunAtLoad"])
        self.assertEqual(data["EnvironmentVariables"]["CODEX_HOME"],str(self.codex_home))
        self.assertEqual(data["EnvironmentVariables"]["CODEX_BIN"],self.codex)
        self.assertEqual(data["ProgramArguments"][0],str(self.bin/"python3"))
        result=subprocess.run(["sh","-c",self.registered()[0]["command"]+" --refresh"], env={**os.environ,"HOME":str(self.home),"CODEX_HOME":"/deliberately/wrong"}, capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr)
        out=self.codex_home/"runcat-usage.json"
        self.assertEqual(json.loads(out.read_text())["metricsBarValue"],"39%")
        self.assertFalse((self.home/".codex/runcat-usage.json").exists())
        snapshot=out.read_bytes()
        self.run_setup("uninstall.sh")
        self.assertEqual(json.loads(self.hooks.read_text()),self.unrelated)
        self.assertFalse(self.script.exists())
        self.assertFalse(self.wrapper.exists())
        self.assertFalse(self.plist.exists())
        self.assertEqual(out.read_bytes(),snapshot)

    def test_default_home_path_remains_dot_codex(self):
        env=dict(self.env)
        env.pop("CODEX_HOME")
        self.run_setup(env=env)
        self.assertTrue((self.home/".codex/runcat-neo-hook.py").exists())
        data=plistlib.loads(self.plist.read_bytes())
        self.assertEqual(data["EnvironmentVariables"]["CODEX_HOME"],str(self.home/".codex"))

    def test_invalid_json_is_rejected_before_any_install_write(self):
        self.hooks.write_bytes(b"not json")
        before=set(self.codex_home.iterdir())
        self.run_setup(expected=1)
        self.assertEqual(self.hooks.read_bytes(),b"not json")
        self.assertEqual(set(self.codex_home.iterdir()),before)
        self.assertFalse(self.plist.exists())
        self.assertFalse((self.root/"calls").exists())

    def test_invalid_structure_is_rejected(self):
        for data in ([],{"hooks":[]},{"hooks":{"Stop":{}}}):
            with self.subTest(data=data):
                self.hooks.write_text(json.dumps(data))
                self.run_setup(expected=1)
                self.assertEqual(json.loads(self.hooks.read_text()),data)
                self.assertFalse(self.script.exists())

    def test_launchctl_failure_rolls_back_installed_files(self):
        self.run_setup()
        self.script.write_bytes(b"previous installed script")
        paths=[self.script,self.wrapper,self.hooks,self.plist]
        before={p:p.read_bytes() for p in paths}
        (self.root/"fail_next").touch()
        self.run_setup(expected=1)
        self.assertEqual({p:p.read_bytes() for p in paths},before)
        self.assertTrue((self.root/"loaded").exists())

    def test_first_install_failure_removes_new_files_and_preserves_old_hooks(self):
        before=self.hooks.read_bytes()
        (self.root/"fail_next").touch()
        self.run_setup(expected=1)
        self.assertEqual(self.hooks.read_bytes(),before)
        self.assertFalse(self.script.exists())
        self.assertFalse(self.wrapper.exists())
        self.assertFalse(self.plist.exists())

    def test_missing_codex_does_not_alter_configuration(self):
        env={**self.env,"CODEX_BIN":str(self.root/"nonexistent")}
        before=self.hooks.read_bytes()
        self.run_setup(expected=1,env=env)
        self.assertEqual(self.hooks.read_bytes(),before)
        self.assertFalse(self.script.exists())

    def test_symlink_target_is_rejected(self):
        target=self.root/"keep"
        target.write_bytes(b"keep")
        self.script.symlink_to(target)
        self.run_setup(expected=1)
        self.assertEqual(target.read_bytes(),b"keep")
        self.assertTrue(self.script.is_symlink())

    def test_different_codex_home_is_not_silently_replaced(self):
        self.run_setup()
        before=self.plist.read_bytes()
        env={**self.env,"CODEX_HOME":str(self.home/"different")}
        self.run_setup(expected=1,env=env)
        self.assertEqual(self.plist.read_bytes(),before)

    def test_output_override_reaches_both_refresh_paths(self):
        output=self.home/"different output.json"
        self.run_setup(env={**self.env,"RUNCAT_OUT_FILE":str(output)})
        data=plistlib.loads(self.plist.read_bytes())
        self.assertEqual(data["EnvironmentVariables"]["RUNCAT_OUT_FILE"],str(output))
        subprocess.run([str(self.wrapper),"--refresh"],check=True,env={**os.environ,"HOME":str(self.home)},timeout=8)
        self.assertTrue(output.exists())


    def test_local_verifier_on_synthetic_install(self):
        self.run_setup()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/verify_local.py")],
                                env=self.env, capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LOCAL CHECK PASSED", result.stdout)

    def test_local_verifier_rejects_modified_installed_source(self):
        self.run_setup()
        self.script.write_bytes(b"not the package")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/verify_local.py")],
                                env=self.env, capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match", result.stderr)

    def test_latest_transcript_filename_is_json_escaped(self):
        self.run_setup()
        sessions = self.codex_home / "sessions"
        sessions.mkdir()
        transcript = sessions / 'quotes" backslash\\ 한글.jsonl'
        transcript.write_text(json.dumps({"payload": {"type": "token_count", "rate_limits": {
            "primary": {"used_percent": 25, "window_minutes": 10080}}}}))
        result = subprocess.run(["sh", str(ROOT / "scripts/test-latest.sh")], env=self.env,
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"metricsBarValue": "75%"', result.stdout)


if __name__ == "__main__":
    unittest.main()
