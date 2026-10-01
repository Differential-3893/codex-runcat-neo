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
        self.assertEqual(hook.plan_name("prolite"), "Pro")
        self.assertEqual(hook.plan_name("pro"), "Pro (More)")
        self.assertEqual(hook.plan_name("promax"), "Pro (Max)")
        self.assertEqual(hook.plan_name("business"), "Business")
        self.assertEqual(hook.plan_name("future_plan"), "Future Plan")

    def test_selects_longest_window(self):
        windows = [
            {"windowDurationMins": 300, "usedPercent": 10},
            {"windowDurationMins": 10080, "usedPercent": 61},
        ]
        chosen = hook.select_main_quota_window(windows)
        self.assertEqual(chosen["windowDurationMins"], 10080)

    def test_credit_metrics(self):
        cases = [
            ({"hasCredits": True, "balance": "1250.49"}, "1,250"),
            ({"hasCredits": True, "balance": "1250.50"}, "1,251"),
            ({"hasCredits": True, "balance": "1250.99"}, "1,251"),
            ({"hasCredits": True, "balance": " 1250.00 "}, "1,250"),
            ({"hasCredits": True, "balance": "0.001"}, "0"),
            ({"hasCredits": True, "balance": "0.50"}, "1"),
            ({"hasCredits": False, "balance": "0"}, "0"),
            ({"unlimited": True, "balance": "0"}, "Unlimited"),
            ({"hasCredits": True, "balance": None}, "Available"),
            ({"hasCredits": True, "balance": "invalid"}, "Available"),
            ({"hasCredits": False, "balance": None}, None),
            ({"balance": "NaN"}, None),
            ({"balance": "Infinity"}, None),
            ({"balance": "-1"}, None),
            ({"balance": True}, None),
            ({}, None),
            (None, None),
        ]
        for credits, expected in cases:
            with self.subTest(credits=credits):
                metrics = hook.credit_metrics({"credits": credits})
                self.assertEqual(
                    metrics,
                    [] if expected is None else [
                        {"title": "Credits Remaining", "formattedValue": expected}
                    ],
                )
        self.assertEqual(hook.credit_metrics(None), [])

    def test_fetch_account_data_filters_credit_metadata(self):
        credits = {"hasCredits": True, "unlimited": False, "balance": "1250.50"}
        response = {
            "accountId": "synthetic-account-id",
            "rateLimits": {
                "credits": {**credits, "accessToken": "synthetic-secret"},
            },
        }
        with mock.patch.object(hook, "find_codex", return_value="codex"), \
             mock.patch.object(hook.subprocess, "Popen"), \
             mock.patch.object(hook, "read_rpc_response", side_effect=[{}, response]):
            account = hook.fetch_account_data()
        self.assertEqual(account["credits"], credits)
        self.assertNotIn("synthetic-", json.dumps(account))

    def test_background_credits_follow_current_account(self):
        quota = {"usedPercent": 61, "windowDurationMins": 10080}
        account_a = {
            "primary": quota,
            "credits": {"hasCredits": True, "balance": "25"},
        }
        account_b = {"primary": quota, "credits": None}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "runcat.json"
            with mock.patch.object(hook, "OUT", out), mock.patch.object(
                hook, "fetch_account_data",
                side_effect=[account_a, account_b, RuntimeError("offline")],
            ) as fetch:
                self.assertTrue(hook.refresh_from_account())
                data = json.loads(out.read_text())
                self.assertIn(
                    {"title": "Credits Remaining", "formattedValue": "25"},
                    data["metrics"],
                )
                self.assertTrue(hook.refresh_from_account())
                data = json.loads(out.read_text())
                self.assertNotIn("Credits Remaining", [m["title"] for m in data["metrics"]])
                previous = out.read_bytes()
                self.assertFalse(hook.refresh_from_account())
                self.assertEqual(out.read_bytes(), previous)
                self.assertEqual(fetch.call_count, 3)

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
                        "credits": {"has_credits": True, "balance": "999"},
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
                "credits": {"hasCredits": True, "balance": "1250.50"},
                "resetCoupons": {"availableCount": 2, "credits": []},
            }

            with mock.patch.object(hook, "OUT", out), mock.patch.object(
                hook, "account_data", return_value=account
            ):
                wrote = hook.write_snapshot({"transcript_path": str(transcript)})

            self.assertTrue(wrote)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["metricsBarValue"], "39%")
            self.assertEqual(data["symbol"], "apple.terminal")

            metrics = {item["title"]: item for item in data["metrics"]}
            self.assertEqual(metrics["Plan"]["formattedValue"], "Pro (More)")
            self.assertEqual(metrics["Credits Remaining"]["formattedValue"], "1,251")
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
            self.assertEqual(data["symbol"], "apple.terminal")
            metrics = {item["title"]: item for item in data["metrics"]}
            self.assertEqual(metrics["Plan"]["formattedValue"], "Pro (More)")
            self.assertEqual(metrics["Weekly Remaining"]["formattedValue"], "39%")
            self.assertEqual(metrics["Reset Coupons"]["formattedValue"], "2")
            self.assertIn("Next Expiry", metrics)


if __name__ == "__main__":
    unittest.main()
