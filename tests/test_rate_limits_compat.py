"""Synthetic protocol contract: single-bucket card in a multi-bucket response."""
from __future__ import annotations
import copy
import io
import json
import random
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_regressions import ROOT, hook

FIXTURE = json.loads((ROOT/'tests/fixtures/rate_limits_compat.json').read_text(encoding='utf-8'))


class RateLimitCompatibilityTests(unittest.TestCase):
    def response(self):
        return copy.deepcopy(FIXTURE['response'])

    def render(self, response, transcript=None):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'usage.json'
            hook_input = {}
            if transcript is not None:
                p = Path(tmp)/'session.jsonl'
                p.write_text(json.dumps({'payload': {'type':'token_count','rate_limits':transcript}})+'\n')
                hook_input['transcript_path'] = str(p)
            with mock.patch.object(hook, 'OUT', out), \
                 mock.patch.object(hook, 'account_data', return_value=hook.safe_account_data(response)):
                wrote = hook.write_snapshot(hook_input)
            return json.loads(out.read_text()) if wrote else None

    def test_legacy_and_multi_bucket_responses_keep_identical_display(self):
        multi = self.response()
        legacy = copy.deepcopy(multi)
        del legacy['rateLimitsByLimitId']
        first, second = self.render(legacy), self.render(multi)
        first.pop('lastUpdatedDate'); second.pop('lastUpdatedDate')
        self.assertEqual(first, second)
        self.assertEqual(second['metricsBarValue'], '39%')

    def test_single_view_wins_even_when_named_codex_map_entry_disagrees(self):
        response = self.response()
        response['rateLimitsByLimitId']['codex']['primary']['usedPercent'] = 99
        self.assertEqual(self.render(response)['metricsBarValue'], '39%')

    def test_other_bucket_longer_window_cannot_replace_main_window(self):
        response = self.response()
        other = response['rateLimitsByLimitId']['other_meter']
        other['primary'].update(windowDurationMins=999999, usedPercent=99)
        data = self.render(response)
        self.assertEqual(data['metricsBarValue'], '39%')
        self.assertIn('Weekly Remaining', [m['title'] for m in data['metrics']])

    def test_map_order_does_not_change_selection(self):
        response = self.response()
        rng = random.Random(412)
        response['rateLimitsByLimitId'].update({f'unknown_{i}': {'primary':{'usedPercent':i}} for i in range(16)})
        original = hook.safe_account_data(response)
        pairs = list(response['rateLimitsByLimitId'].items())
        for _ in range(20):
            rng.shuffle(pairs)
            response['rateLimitsByLimitId'] = dict(pairs)
            self.assertEqual(hook.safe_account_data(response), original)

    def test_absent_null_and_malformed_optional_maps_are_ignored(self):
        response = self.response()
        expected = hook.safe_account_data(response)
        for value in (None, {}, [], True, 'unknown', {'unknown':None}):
            response['rateLimitsByLimitId'] = value
            self.assertEqual(hook.safe_account_data(response), expected)

    def test_map_only_response_preserves_previous_snapshot_and_timestamp(self):
        for value in (None, {}, [], 'invalid'):
            response = self.response()
            response['rateLimits'] = value
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)/'saved.json'
                previous = b'{"old":true,"lastUpdatedDate":"2000-01-01T00:00:00Z"}'
                out.write_bytes(previous)
                with mock.patch.object(hook, 'OUT', out), \
                     mock.patch.object(hook, 'account_data', return_value=hook.safe_account_data(response)):
                    self.assertFalse(hook.refresh_from_account())
                self.assertEqual(out.read_bytes(), previous)

    def test_no_cross_bucket_plan_or_credit_fallback(self):
        response = self.response()
        response['rateLimits'].pop('credits')
        response['rateLimits'].pop('planType')
        data = self.render(response)
        names = {m['title'] for m in data['metrics']}
        self.assertNotIn('Plan', names)
        self.assertNotIn('Credits Remaining', names)
        self.assertIn('Reset Coupons', names)

    def test_selected_credits_and_top_level_coupons_keep_existing_semantics(self):
        metrics = {m['title']:m['formattedValue'] for m in self.render(self.response())['metrics']}
        self.assertEqual(metrics['Plan'], 'Pro 200')
        self.assertEqual(metrics['Credits Remaining'], '1,251')
        self.assertEqual(metrics['Reset Coupons'], '2')
        self.assertIn('Next Expiry', metrics)

    def test_extra_identity_banners_and_private_bucket_metadata_are_not_output(self):
        response = self.response()
        response.update(accountId='PRIVATE_ACCOUNT', rateLimitUpsell={'text':'PRIVATE_BANNER'})
        response['rateLimitsByLimitId']['other_meter'].update(email='PRIVATE_EMAIL', planType='PRIVATE_PLAN')
        response['rateLimitsByLimitId']['private_id'] = {'accessToken':'PRIVATE_TOKEN'}
        for output in (hook.safe_account_data(response), self.render(response)):
            text = json.dumps(output)
            self.assertNotIn('PRIVATE_', text)
            self.assertNotIn('rateLimitsByLimitId', text)

    def test_permission_flag_does_not_change_percentages_or_pretend_to_grant_usage(self):
        baseline = None
        for allowed in (True, False, None):
            response = self.response()
            response['ordinaryUsageAllowed'] = allowed
            data = self.render(response)
            data.pop('lastUpdatedDate')
            if baseline is None:
                baseline = data
            self.assertEqual(data, baseline)
            self.assertNotIn('ordinaryUsageAllowed', data)

    def test_valid_secondary_in_same_bucket_wins_over_invalid_primary(self):
        response = self.response()
        response['rateLimits']['primary']['usedPercent'] = None
        data = self.render(response)
        self.assertEqual(data['metricsBarValue'], '80%')
        self.assertIn('5h Remaining', [m['title'] for m in data['metrics']])

    def test_stop_transcript_precedence_is_not_changed_by_extra_account_buckets(self):
        data = self.render(self.response(), {'plan_type':'plus', 'primary': {
            'used_percent':25, 'window_minutes':10080, 'resets_at':1791000000}})
        metrics = {m['title']:m['formattedValue'] for m in data['metrics']}
        self.assertEqual(data['metricsBarValue'], '75%')
        self.assertEqual(metrics['Plan'], 'Pro 200')
        self.assertEqual(metrics['Credits Remaining'], '1,251')

    def test_pinned_fixture_documents_contract_and_synthetic_origin(self):
        self.assertEqual(FIXTURE['upstream_commit'], 'c5d242fa7907bff1b7a7e26e95febc548c0a6963')
        self.assertTrue(FIXTURE['synthetic'])
        note = (ROOT/'docs/VERIFICATION_COMPATIBILITY_20261003.md').read_text()
        for word in ('rateLimitsByLimitId', 'rateLimits', 'single-bucket', '--verify-stop'):
            self.assertIn(word, note)
