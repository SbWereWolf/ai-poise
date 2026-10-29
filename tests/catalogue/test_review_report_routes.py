"""Execute the shipped review routes; the expected route is test-owned."""
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("scenario", ["short", "feedback"])
def test_shipped_review_completes_with_independent_report_inspection(tmp_path, monkeypatch, scenario):
    monkeypatch.syspath_prepend(str(ROOT / "examples"))
    from catalogue_walkthrough import run

    result = run(tmp_path / scenario, "review", scenario)
    assert result["status"] == "PASS"
    assert result["task_status"] == "completed"
    assert result["goal_type"] == "review"
    assert result["remote_unchanged"]
    assert result["open_findings_at_completion"] == 0
    assert {r["stage"] for r in result["reports"]} == {
        "planning", "planning_inspection", "inspection", "execution_inspection",
    }
    assert len(result["reports"]) == len(result["accepted_stages"])
    assert result["read_only_checks"] == len(result["reports"])
    assert result["ready_artifacts"] >= 1
    if scenario == "short":
        assert [r["stage"] for r in result["reports"]] == [
            "planning", "planning_inspection", "inspection", "execution_inspection",
        ]
        assert result["feedback_cycles"] == 0
    else:
        assert result["feedback_cycles"] >= 1
        assert max(r["iteration"] for r in result["reports"]) >= 2
