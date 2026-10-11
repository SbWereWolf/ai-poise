"""Integration subject: real Requirements CLI/application/store, independent SQL oracle."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from poise.composition import requirements_tools
from requirements_registry.crud_support import (
    arrange, assert_effects, invoke, literal, snapshot,
)


CASES = literal("crud_cases.json") + literal("crud_edges.json")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_public_crud_exact_contract_and_atomic_effects(project, tmp_path, case):
    database, _, config_path = arrange(project, tmp_path)
    for step in case["steps"]:
        before = snapshot(database)
        # Invalid decoded scalar values are sent as legal UTF-8 JSON escapes.
        escaped = step.get("escaped", False) or (
            case["id"].startswith("B08-") and case["id"] != "B08-valid"
        )
        code, response = invoke(config_path, step["request"], escaped=escaped)
        assert response == step["response"]
        assert code == (2 if step["response"]["status"] == "rejected" else 0)
        assert_effects(before, snapshot(database), step)


def test_direct_application_query_has_no_cli_status(project, tmp_path):
    database, _, config_path = arrange(project, tmp_path)
    case = next(case for case in CASES if case["id"] == "E01")
    step = case["steps"][0]
    commands, _ = requirements_tools(config_path)
    before = snapshot(database)
    expected = deepcopy(step["response"])
    del expected["status"]
    assert commands.query(step["request"]["input"]["queries"]) == expected
    assert snapshot(database) == before


@pytest.mark.parametrize("case_id", ["E01", "E09", "W02"])
def test_real_module_cli_uses_this_child_source(project, tmp_path, case_id):
    database, _, config_path = arrange(project, tmp_path)
    case = next(case for case in CASES if case["id"] == case_id)
    step = case["steps"][0]
    before = snapshot(database)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "poise", "requirements", "--config", str(config_path)],
        input=json.dumps(step["request"], ensure_ascii=False).encode("utf-8"),
        capture_output=True, env=environment, check=False, timeout=15,
    )
    assert result.stderr == b""
    assert result.stdout.endswith(b"\n") and result.stdout.count(b"\n") == 1
    assert json.loads(result.stdout) == step["response"]
    assert result.returncode == (2 if case_id == "W02" else 0)
    assert_effects(before, snapshot(database), step)


def test_unicode_fixtures_exercise_decoded_surrogates_not_literal_backslashes():
    high = next(case for case in CASES if case["id"] == "B08-filter-high")
    low = next(case for case in CASES if case["id"] == "B08-filter-low")
    for case, expected in [(high, 0xD800), (low, 0xDFFF)]:
        text = case["steps"][0]["request"]["input"]["queries"][0]["filter"]["text"]["eq"]
        assert len(text) == 1 and ord(text) == expected
        encoded = json.dumps(case["steps"][0]["request"], ensure_ascii=True).encode("utf-8")
        decoded = json.loads(encoded)["input"]["queries"][0]["filter"]["text"]["eq"]
        assert len(decoded) == 1 and ord(decoded) == expected
    valid = next(case for case in CASES if case["id"] == "B08-valid")
    packet = valid["steps"][0]["request"]
    escaped = json.dumps(packet, ensure_ascii=True).encode("utf-8")
    assert b"\\ud83d\\ude00" in escaped
    assert json.loads(escaped)["input"]["operations"][0]["row"]["text"] == "Заказ 😀"
