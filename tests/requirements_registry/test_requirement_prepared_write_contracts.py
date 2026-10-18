"""Integration subject: RequirementsStore.apply's exact replay and atomic result."""
from copy import deepcopy
import sqlite3
from unittest.mock import patch

from independent_write_support import RequirementCase, concurrent_pair, execute, rows
from poise.modules.foundation.errors import DomainError, VersionConflict
from poise.modules.requirements_registry.domain import RequirementsRegistry


class RequirementWriteContracts(RequirementCase):
    def test_concurrent_exact_intents_return_one_saved_business_result(self):
        results = concurrent_pair(self.apply)
        self.assertEqual(sorted(item['replayed'] for item in results), [False, True])
        self.assert_expected('expected_after_a.json')

    def test_multirow_success_and_exact_replay_preserve_one_effect(self):
        self.assertEqual(self.apply(), {'revision': 1, 'replayed': False})
        self.assert_expected('expected_after_a.json')
        before = self.snapshot()
        self.assertEqual(self.apply(), {'revision': 1, 'replayed': True})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(rows(self.path, 'PRAGMA integrity_check'), [('ok',)])
        self.assertEqual(rows(self.path, 'PRAGMA foreign_key_check'), [])

    def test_changed_digest_and_stale_revision_preserve_accepted_result(self):
        self.apply()
        before = self.snapshot()
        changed = deepcopy(self.request)
        changed['operations'][0]['requirement']['text'] = 'Different intent.'
        with self.assertRaises(DomainError):
            self.apply(changed)
        stale = deepcopy(self.request)
        stale['request_id'] = 'another-request'
        with self.assertRaises(VersionConflict):
            self.apply(stale)
        self.assertEqual(self.snapshot(), before)

    def test_pure_preparation_failure_has_no_effect_and_no_body_replay(self):
        before = self.snapshot()
        fault = RuntimeError('test-owned registry preparation fault')
        with patch.object(RequirementsRegistry, 'apply', side_effect=fault) as prepare:
            with self.assertRaises(RuntimeError) as caught:
                self.apply()
            self.assertIs(caught.exception, fault)
            self.assertEqual(prepare.call_count, 1)
        self.assertEqual(self.snapshot(), before)

    def test_receipt_write_failure_rolls_back_data_links_revision_and_response(self):
        before = self.snapshot()
        execute(self.path, 'CREATE TRIGGER fail_receipt BEFORE INSERT ON requirement_requests '
                "BEGIN SELECT RAISE(ABORT,'test receipt fault'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.apply()
        self.assertEqual(self.snapshot(), before)
        execute(self.path, 'DROP TRIGGER fail_receipt')
        self.apply()
        self.assert_expected('expected_after_a.json')

    def test_invalid_request_and_missing_parent_preserve_exact_state(self):
        before = self.snapshot()
        for request in (
            {},
            dict(self.request, expected_revision='0'),
            dict(self.request, operations=[{'kind': 'link', 'system': 'absent', 'application': 'APP-A'}]),
        ):
            with self.subTest(request=request), self.assertRaises(DomainError):
                self.apply(request)
            self.assertEqual(self.snapshot(), before)
