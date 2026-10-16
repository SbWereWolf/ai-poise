"""Independent controls for the real missing-executable diagnostic assertion."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


def check_reason(monkeypatch, tmp_path, reason):
    specification = importlib.util.spec_from_file_location(
        "unicode_bootstrap_assertions",
        Path(__file__).with_name("test_bootstrap_commands.py"),
    )
    subject = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(subject)
    root = tmp_path / "Poise рабочая копия"
    expected_path = str(root / "missing-python")
    reply = {
        "status": "error",
        "reason": "Cannot launch: " + expected_path if reason is None else reason,
        "unrelated_path": expected_path,
    }
    monkeypatch.setattr(subject, "invoke", lambda *_: subprocess.CompletedProcess(
        [], 4, json.dumps(reply).encode("utf-8"), b""))
    monkeypatch.setattr(subject, "calls", lambda _: [])
    subject.test_missing_executable_reports_available_diagnostics(
        {"root": root, "command": {"parameters": {}}})


def test_correct_unicode_reason_is_accepted(monkeypatch, tmp_path):
    check_reason(monkeypatch, tmp_path, None)


@pytest.mark.parametrize("reason", ["", "wrong executable"])
def test_lost_or_wrong_reason_is_rejected_with_path_elsewhere(monkeypatch, tmp_path, reason):
    with pytest.raises(AssertionError):
        check_reason(monkeypatch, tmp_path, reason)
