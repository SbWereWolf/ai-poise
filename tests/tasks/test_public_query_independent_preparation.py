"""Real public readers must release blocking SQL before pure preparation."""
from public_query_support import PublicQueryCase


class IndependentPublicQueryPreparation(PublicQueryCase):
    def test_content_preparation_allows_independent_commit(self):
        self.assert_public_causal('content')

    def test_evidence_preparation_allows_independent_commit(self):
        self.assert_public_causal('evidence')

    def test_registry_preparation_allows_independent_commit(self):
        self.assert_public_causal('registry')

    def test_record_preparation_allows_independent_commit(self):
        self.assert_public_causal('record')

    def test_current_preparation_allows_independent_commit(self):
        self.assert_public_causal('current')

    def test_accounting_snapshot_preparation_allows_independent_commit(self):
        self.assert_public_causal('accounting_snapshot')

    def test_accounting_nonblocking_preparation_allows_independent_commit(self):
        self.assert_public_causal('accounting_nonblocking')

    def test_criteria_preparation_allows_independent_commit(self):
        self.assert_public_causal('criteria')

    def test_pending_receipt_preparation_allows_independent_commit(self):
        self.assert_public_causal('pending_receipt')

    def test_resolve_path_projection_allows_independent_commit(self):
        self.assert_public_causal('resolve_path')

    def test_trace_projection_allows_independent_commit(self):
        self.assert_public_causal('trace')

    def test_submission_projection_allows_independent_commit(self):
        self.assert_public_causal('submission')

    def test_result_projection_allows_independent_commit(self):
        self.assert_public_causal('result')

    def test_terminal_projection_allows_independent_commit(self):
        self.assert_public_causal('terminal')

    def test_history_projection_allows_independent_commit(self):
        self.assert_public_causal('history')

    def test_summary_projection_allows_independent_commit(self):
        self.assert_public_causal('summary')

    def test_standalone_summary_projection_allows_independent_commit(self):
        self.assert_public_causal('standalone_summary')
