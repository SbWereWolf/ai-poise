"""The test oracle rejects contradictory outcome copies at the real envelope boundary."""
import json
from pathlib import Path

import pytest

from poise.modules.result_views.domain import bounded_envelope
from .test_terminal_receipt_views import historical_outcome


CASES = json.loads((Path(__file__).parent / 'fixtures/terminal_outcome_oracle.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=[case['name'] for case in CASES])
def test_outcome_oracle_checks_every_displayed_copy(case):
    raw = {'status': 'read_only', 'checks': case['checks']}
    visible = json.loads(bounded_envelope(raw, None, 10000, {}, {}, case['command_views']))
    if case['accepted']:
        historical_outcome(visible, 'R1', 'CHECK', 7, False)
    else:
        with pytest.raises(AssertionError, match='Historical receipt'):
            historical_outcome(visible, 'R1', 'CHECK', 7, False)
