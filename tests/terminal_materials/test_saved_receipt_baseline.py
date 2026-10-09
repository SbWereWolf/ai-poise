"""Verify immutable accepted baseline evidence; not current implementation GREEN."""
import hashlib
import json
import os
from pathlib import Path


def test_saved_baseline_records_actual_historical_integration_display():
    result = Path(os.environ['RECEIPT_BASELINE_RESULT']).read_bytes()
    source = Path(os.environ['RECEIPT_BASELINE_QUERY']).read_bytes()
    assert hashlib.sha256(result).hexdigest() == (
        'ec3b9e85c47bc738612808f6ac8d4a56366cefa84a1082a16da73baf78eea7e5')
    assert hashlib.sha256(source).hexdigest() == (
        'b3a0618234120008e4883d5c054468658400d80b9619b87fb7bd1b7136f01ec8')
    baseline = json.loads(result)
    integration = json.loads(source)['results'][0]['value']
    assert baseline['commit'] == '746f55a70cdbf3e489c0e0622aaa16e9ec862bb5'
    assert baseline['task'] == integration['task'] == 'AIP-TERMINAL-TASK-MATERIAL-CLEANUP-001'
    assert baseline['terminal'] is True and integration['status'] == 'integrated'
    assert baseline['receipt_count'] == len(integration['checks']) == 13
    assert len(baseline['actual_views']) == 13
    assert {row['presentation_error'] for row in baseline['actual_views']} == {
        'Captured stdout no longer equals execution receipt'}
    assert baseline['requested_notice_present'] is False
    assert baseline['full_saved_response_has_notice'] is False
    assert baseline['historical_result_preserved'] is True
