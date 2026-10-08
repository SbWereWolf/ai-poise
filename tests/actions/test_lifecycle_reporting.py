"""Exact RED summaries retain every pytest phase and unexpected outcome."""
from types import SimpleNamespace
import json
from pathlib import Path

from .lifecycle_reporting import LifecycleReport


FIXTURES = Path(__file__).parent / "fixtures"


def report(nodeid, phase, outcome, message=""):
    return SimpleNamespace(nodeid=nodeid, when=phase, outcome=outcome,
                           failed=outcome == "failed", skipped=outcome == "skipped",
                           longrepr=SimpleNamespace(reprcrash=SimpleNamespace(message=message)))


def test_report_keeps_setup_call_teardown_and_unexpected_failure():
    plugin = LifecycleReport()
    plugin.pytest_runtest_logreport(report("a", "setup", "passed"))
    plugin.pytest_runtest_logreport(report("a", "call", "failed", "EXPECTED"))
    plugin.pytest_runtest_logreport(report("a", "teardown", "failed", "UNEXPECTED"))
    expected = json.loads((FIXTURES / "lifecycle_reporting_phases.json").read_text())
    assert plugin.summary(1)["reports"] == expected


def test_report_keeps_collection_errors_and_skip_diagnostics():
    plugin = LifecycleReport()
    plugin.pytest_collectreport(SimpleNamespace(nodeid="bad", failed=True,
                                               skipped=False, outcome="failed", longrepr="IMPORT_ERROR"))
    skipped = report("s", "call", "skipped")
    skipped.longrepr = ("source.py", 7, "UNEXPECTED_SKIP")
    plugin.pytest_runtest_logreport(skipped)
    result = plugin.summary(2)
    assert result["exit_code"] == 2
    expected = json.loads((FIXTURES / "lifecycle_reporting_collection.json").read_text())
    assert result["collection"] == expected["collection"]
    assert result["reports"] == expected["reports"]
