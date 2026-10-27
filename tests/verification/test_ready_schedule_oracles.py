"""Independent counterexamples for both diagnostic assertion boundaries."""
import json
from pathlib import Path

import pytest

from poise.modules.foundation.errors import DomainError
from tasks.test_ready_schedule_boundary import assert_ready_diagnostic
from verification.test_ready_schedule_diagnostics import assert_diagnostic


DATA = json.loads((Path(__file__).parent / "fixtures/ready_schedule_oracle_cases.json").read_text())


@pytest.mark.parametrize("oracle", [assert_diagnostic, assert_ready_diagnostic], ids=["registry", "ready"])
@pytest.mark.parametrize("case", DATA["cases"], ids=lambda case: case["id"])
def test_preservation_oracle_rejects_false_guidance_and_wrong_stage(oracle, case):
    error = DomainError(case["message"])
    if case["accepted"]:
        oracle(error, DATA["asserted_case"])
    else:
        with pytest.raises(AssertionError):
            oracle(error, DATA["asserted_case"])
