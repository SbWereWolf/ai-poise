"""Test the reviewed assertions, not the product, with explicit control replies.

These controls accompany the real CLI tests; they never substitute for them.
"""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


DIRECTORY = Path(__file__).parent
SCENARIOS = [("repair-failure", [2, 4, 2]), ("verify-failure", [2, 0, 2])]


def load_test(name):
    specification = importlib.util.spec_from_file_location(name, DIRECTORY / f"{name}.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def diagnostic_control(monkeypatch, scenario, codes, *, index=None, field=None, missing=False, dropped=False):
    subject = load_test("test_bootstrap_commands")
    observations = json.loads((DIRECTORY / "fixtures" / f"maintenance_{scenario}_expected.json").read_text())
    observations = deepcopy(observations)
    if dropped:
        observations.pop()
    elif missing:
        del observations[index]["stdout"]
    elif field:
        observations[index][field] = 999 if field == "exit_code" else "LOST"
    reply = {"status": "error", "actions": ["repair", "verify"], "observations": observations}
    monkeypatch.setattr(subject, "invoke", lambda *_: subprocess.CompletedProcess(
        [], 4, json.dumps(reply).encode("utf-8"), b""))
    monkeypatch.setattr(subject, "calls", lambda _: [{"argv": [name]} for name in ["run", "repair", "verify"]])
    subject.test_second_error_returns_both_diagnostics_without_second_repair(
        {"tool": {}}, scenario, codes)


@pytest.mark.parametrize("scenario,codes", SCENARIOS)
def test_full_independent_diagnostics_are_accepted(monkeypatch, scenario, codes):
    diagnostic_control(monkeypatch, scenario, codes)


@pytest.mark.parametrize("scenario,codes", SCENARIOS)
@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("field", ["stdout", "stderr", "stage", "exit_code"])
def test_substituted_observation_is_rejected(monkeypatch, scenario, codes, index, field):
    with pytest.raises(AssertionError):
        diagnostic_control(monkeypatch, scenario, codes, index=index, field=field)


@pytest.mark.parametrize("scenario,codes", SCENARIOS)
@pytest.mark.parametrize("index", [0, 1, 2])
def test_lost_stdout_is_rejected(monkeypatch, scenario, codes, index):
    with pytest.raises(AssertionError):
        diagnostic_control(monkeypatch, scenario, codes, index=index, missing=True)


@pytest.mark.parametrize("scenario,codes", SCENARIOS)
def test_dropped_observation_is_rejected(monkeypatch, scenario, codes):
    with pytest.raises(AssertionError):
        diagnostic_control(monkeypatch, scenario, codes, dropped=True)


def example_control(monkeypatch, tmp_path, value):
    subject = load_test("test_bootstrap_configuration")
    config = tmp_path / "config"
    config.mkdir()
    (config / "environment-maintenance.example.json").write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(subject, "ROOT", tmp_path)
    subject.test_external_tool_config_has_a_versioned_example()


def test_populated_external_example_is_accepted(monkeypatch, tmp_path):
    example_control(monkeypatch, tmp_path, {"python": "/srv/poise/bootstrap-venv/bin/python"})


@pytest.mark.parametrize("value", [{}, {"python": ""}, {"python": 34}, {"python": "relative-python"}])
def test_empty_or_unusable_external_example_is_rejected(monkeypatch, tmp_path, value):
    with pytest.raises(AssertionError):
        example_control(monkeypatch, tmp_path, value)
