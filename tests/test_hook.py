import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = ROOT / "runcat-neo-hook.py"

spec = importlib.util.spec_from_file_location("runcat_hook", HOOK_PATH)
hook = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(hook)


class HookTests(unittest.TestCase):
    def test_plan_mapping(self):
        self.assertEqual(hook.plan_name("free"), "Free")
        self.assertEqual(hook.plan_name("plus"), "Plus")
        self.assertEqual(hook.plan_name("prolite"), "Pro Lite")
        self.assertEqual(hook.plan_name("pro"), "Pro")
        self.assertEqual(hook.plan_name("business"), "Business")
        self.assertEqual(hook.plan_name("future_plan"), "Future Plan")

    def test_selects_longest_window(self):
        windows = [
            {"windowDurationMins": 300, "usedPercent": 10},
            {"windowDurationMins": 10080, "usedPercent": 61},
        ]
        chosen = hook.select_main_quota_window(windows)
        self.assertEqual(chosen["windowDurationMins"], 10080)

    def test_quota_titles(self):
        self.assertEqual(hook.quota_title(1440), "Daily Remaining")
        self.assertEqual(hook.quota_title(10080), "Weekly Remaining")
        self.assertEqual(hook.quota_title(43200), "30d Remaining")
        self.assertEqual(hook.quota_title(300), "5h Remaining")
        self.assertEqual(hook.quota_title(None), "Quota Remaining")

    def test_stop_hook_snapshot_uses_remaining_percentage(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            out = Path(tmp) / "runcat.json"

            payload = {
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "plan_type": "pro",
                        "primary": {
                            "used_percent": 61.0,
                            "window_minutes": 10080,
                            "resets_at": 1790391033,
                        },
                        "secondary": None,
                    },
                }
            }
            transcript.write_text(json.dumps(payload) + "\n", encoding="utf-8")

            account = {
                "planType": "pro",
                "resetCoupons": {"availableCount": 2, "credits": []},
            }

            with mock.patch.object(hook, "OUT", out), mock.patch.object(
                hook, "account_data", return_value=account
            ):
                wrote = hook.write_snapshot({"transcript_path": str(transcript)})

            self.assertTrue(wrote)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["metricsBarValue"], "39%")
            self.assertEqual(data["symbol"], "line.3.crossed.swirl.circle")

            metrics = {item["title"]: item for item in data["metrics"]}
            self.assertEqual(metrics["Plan"]["formattedValue"], "Pro")
            self.assertEqual(metrics["Weekly Remaining"]["formattedValue"], "39%")
            self.assertEqual(metrics["Weekly Remaining"]["normalizedValue"], 0.39)
            self.assertEqual(metrics["Reset Coupons"]["formattedValue"], "2")

    def test_background_refresh_needs_no_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "runcat.json"
            account = {
                "planType": "pro",
                "primary": {
                    "usedPercent": 61.0,
                    "windowDurationMins": 10080,
                    "resetsAt": 1790391033,
                },
                "secondary": None,
                "resetCoupons": {
                    "availableCount": 2,
                    "credits": [
                        {
                            "status": "available",
                            "expiresAt": 1791173986,
                            "title": "Full reset",
                        }
                    ],
                },
            }

            with mock.patch.object(hook, "OUT", out), mock.patch.object(
                hook, "account_data", return_value=account
            ):
                wrote = hook.refresh_from_account()

            self.assertTrue(wrote)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["metricsBarValue"], "39%")
            metrics = {item["title"]: item for item in data["metrics"]}
            self.assertEqual(metrics["Plan"]["formattedValue"], "Pro")
            self.assertEqual(metrics["Weekly Remaining"]["formattedValue"], "39%")
            self.assertEqual(metrics["Reset Coupons"]["formattedValue"], "2")
            self.assertIn("Next Expiry", metrics)


if __name__ == "__main__":
    unittest.main()
