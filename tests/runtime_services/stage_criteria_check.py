"""Bounded standard-pytest evidence; Poise still owns execution and full outputs."""

from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import sys

import pytest


GROUPS = {
    "definitions": "tests/goal_config/test_stage_purposes.py",
    "lifecycle": "tests/runtime_services/test_stage_criteria_lifecycle.py",
    "admission": "tests/runtime_services/test_stage_criteria_admission.py",
    "migration": "tests/migrations/test_stage_criteria_migration.py",
}
RED = {
    "definitions": ("definitions", "test_configuration_does_not_discard_known_purpose_or_authority", "KeyError: 'purpose'"),
    "lifecycle": ("lifecycle", "test_initial_execution_uses_real_initial_criteria_without_proposal_or_decision", "stage contract requires exact fields"),
    "admission": ("admission", "test_design_can_refine_future_target_without_rewriting_shared_bound_criteria_or_proof", "stage contract requires exact fields"),
    "migration": ("migration", "test_real_cli_migrates_complete_historical_inventory_and_exact_replay_preserves_data", "неизвестные поля"),
    "recovery-exit": ("migration", "test_cli_partial_publication_returns_exact_recovery_envelope_and_exit_two", "assert 0 == 2"),
}


class Reports:
    def __init__(self, expected_marker):
        self.marker = expected_marker
        self.collected = 0
        self.passed = 0
        self.failed = []
        self.errors = []

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)

    def pytest_collectreport(self, report):
        if report.failed:
            self.errors.append({"nodeid": report.nodeid, "phase": "collection"})

    def pytest_runtest_logreport(self, report):
        if report.when == "call" and report.passed:
            self.passed += 1
        if report.failed:
            item = {"nodeid": report.nodeid, "phase": report.when}
            if report.when == "call":
                item["intentional"] = self.marker is not None and self.marker in str(report.longrepr)
                self.failed.append(item)
            else:
                self.errors.append(item)


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("red", "green"):
        raise ValueError("An explicit red/green mode and bounded group are required.")
    mode, name = sys.argv[1:]
    if mode == "red":
        group, test, marker = RED[name]
        selectors = [GROUPS[group] + "::" + test]
    else:
        selectors, marker = [GROUPS[name]], None
    reports = Reports(marker)
    output = Path(os.environ["POISE_RUN_OUTPUT_DIR"])
    with (output / "pytest.txt").open("w", encoding="utf-8", buffering=1) as diagnostics:
        with redirect_stdout(diagnostics), redirect_stderr(diagnostics):
            code = int(pytest.main(["-q", "--tb=short", *selectors], plugins=[reports]))
    summary = {
        "collected": reports.collected, "passed": reports.passed,
        "failed": sorted(reports.failed, key=lambda item: (item["nodeid"], item["phase"])),
        "errors": sorted(reports.errors, key=lambda item: (item["nodeid"], item["phase"])),
        "exit_code": code,
    }
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
