"""Subjects: actual example bootstrap and review routes using the real readonly API.

The adapter selects a supported interface; it never supplies a store or seed.
SQLite expectations belong to these tests, independently of example inputs.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from poise import composition


ROOT = Path(__file__).resolve().parents[2]
EXPECTED = Path(__file__).parent / "fixtures/demo-query-only/expected-registry.json"


@pytest.fixture(autouse=True)
def query_only_examples(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "examples"))
    monkeypatch.setenv("LANG", "C.UTF-8")
    original = composition.requirements_tools

    def query_only(config_path, *, read_only=True):
        assert read_only is True
        return original(config_path, read_only=True)

    monkeypatch.setattr(composition, "requirements_tools", query_only)


def assert_registry(home):
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    configuration = json.loads((home / "project.json").read_text(encoding="utf-8"))
    assert configuration["paths"]["state"] == "state"
    assert configuration["paths"]["requirements_database"] == "database/requirements.sqlite"
    database = home / "state/database/requirements.sqlite"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        requirements = [dict(row) for row in connection.execute(
            "SELECT id,level,status,text FROM requirements ORDER BY id"
        )]
        links = [list(row) for row in connection.execute(
            "SELECT system_id,application_id FROM requirement_links "
            "ORDER BY system_id,application_id"
        )]
        revisions = [row[0] for row in connection.execute(
            "SELECT revision FROM requirements_meta"
        )]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert requirements == expected["requirements"]
    assert links == expected["links"]
    assert revisions == [expected["revision"]]
    assert version == expected["schema_version"]


def test_demo_bootstrap_supports_query_only_requirements(tmp_path):
    from demo import create

    home = create(tmp_path / "demo")
    assert_registry(home)
    task = json.loads((home / "task.json").read_text(encoding="utf-8"))
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    assert task["requirements"] == expected["task_requirements"]
    assert task["requirements_snapshot"]
    # This agreement is deliberately synthetic example data.
    assert task["requirements_agreement"]["accepted"] is True
    assert task["requirements_agreement"]["chains"]


@pytest.mark.parametrize("scenario", ["short", "feedback"])
def test_review_routes_support_query_only_requirements(tmp_path, scenario):
    from catalogue_walkthrough import run

    directory = tmp_path / scenario
    result = run(directory, "review", scenario)
    assert result["status"] == "PASS"
    assert result["task_status"] == "completed"
    assert result["goal_type"] == "review"
    assert result["remote_unchanged"]
    assert result["open_findings_at_completion"] == 0
    assert {row["stage"] for row in result["reports"]} == {
        "planning", "planning_inspection", "inspection", "execution_inspection",
    }
    assert len(result["reports"]) == len(result["accepted_stages"])
    assert result["read_only_checks"] == len(result["reports"])
    assert result["ready_artifacts"] >= 1
    if scenario == "short":
        assert [row["stage"] for row in result["reports"]] == [
            "planning", "planning_inspection", "inspection", "execution_inspection",
        ]
        assert result["feedback_cycles"] == 0
    else:
        assert result["feedback_cycles"] >= 1
        assert max(row["iteration"] for row in result["reports"]) >= 2
    assert_registry(directory / "poise")
