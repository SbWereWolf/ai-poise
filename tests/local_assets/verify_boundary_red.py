"""Accept only the declared real boundary failures, saving complete diagnostics."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

import pytest


FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "boundary-red-cases.json").read_text())
ERRORS = json.loads((FIXTURES / "error-contracts.json").read_text())
PREFIX = "tests/local_assets/test_boundaries.py::"
EXPECTED = {PREFIX + case["node"]: case for case in CASES}
MARKER = "RED: fourteen local-asset boundary regressions fail as declared"


class Reports:
    def __init__(self):
        self.collected = []
        self.reports = []

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report):
        self.reports.append({"nodeid": report.nodeid, "when": report.when,
                             "outcome": report.outcome, "longrepr": str(report.longrepr),
                             "properties": dict(report.user_properties)})


def cause_matches(case: dict, observed: dict) -> bool:
    fields = {"case", "exit_code", "selected_target_root", "stdout", "stderr", "reply",
              "changed", "first_published", "second_published", "later_parent_created",
              "selected_git_present"}
    if set(observed) != fields or type(observed["exit_code"]) is not int:
        return False
    changed = observed["changed"]
    roots = {"target", "source", "outside"}
    if case["label"] == "plain-donor" or case["label"].startswith("genuine-donor-"):
        roots.add("donor")
    if case["label"] == "genuine-donor-linked":
        roots.add("primary")
    if not isinstance(changed, dict) or set(changed) != roots:
        return False
    if any(type(value) is not bool for value in changed.values()):
        return False
    flags = ("first_published", "second_published", "later_parent_created", "selected_git_present")
    if any(type(observed[name]) is not bool for name in flags):
        return False
    if any(value for name, value in changed.items() if name != "target"):
        return False
    kind = case["cause"]
    if kind in ("unicode-published", "unicode-later-path", "unicode-preflight"):
        if not (observed["exit_code"] == 1 and observed["stdout"] == ""
                and observed["reply"] is None and "UnicodeEncodeError" in observed["stderr"]):
            return False
        if kind == "unicode-published":
            return changed["target"] and observed["first_published"] and observed["second_published"]
        if kind == "unicode-later-path":
            return (changed["target"] and observed["first_published"]
                    and not observed["second_published"] and observed["later_parent_created"])
        return not any(changed.values()) and not observed["first_published"] and not observed["second_published"]
    if kind in ("project_mismatch", "target_root_mismatch", "invalid_target_root"):
        return (observed["exit_code"] == 2 and observed["stderr"] == ""
                and observed["reply"] == {**ERRORS[kind]["reply"], "asset": None}
                and not any(changed.values())
                and not observed["first_published"] and not observed["second_published"])
    expected = json.loads((FIXTURES / "boundary-restored-reply.json").read_text())
    expected["target_root"] = observed["selected_target_root"]
    return (kind == "accepted-publication" and observed["exit_code"] == 0
            and observed["stderr"] == "" and observed["reply"] == expected
            and changed["target"] and observed["first_published"] and observed["second_published"]
            and (case["label"] != "plain-donor" or not observed["selected_git_present"]))


def matches(exit_code: int, collected: list[str], reports: list[dict]) -> bool:
    if exit_code != 1 or len(collected) != 14 or set(collected) != set(EXPECTED):
        return False
    calls = [report for report in reports if report["when"] == "call"]
    if len(calls) != 14 or {r["nodeid"] for r in calls} != set(EXPECTED):
        return False
    if any(report["outcome"] != "passed" for report in reports if report["when"] != "call"):
        return False
    for nodeid in EXPECTED:
        phases = [report["when"] for report in reports if report["nodeid"] == nodeid]
        if sorted(phases) != ["call", "setup", "teardown"]:
            return False
    if any(report["nodeid"] not in EXPECTED for report in reports):
        return False
    for report in calls:
        if report["outcome"] != "failed":
            return False
        observed = report["properties"].get("boundary_observation")
        if not isinstance(observed, dict):
            return False
        case = EXPECTED[report["nodeid"]]
        if observed.get("case") != case["label"]:
            return False
        try:
            if not cause_matches(case, observed):
                return False
        except (KeyError, TypeError):
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostics-directory", type=Path, required=True)
    args = parser.parse_args()
    if not args.diagnostics_directory.is_absolute():
        parser.error("diagnostics-directory must explicitly select an absolute configured-state path")
    observer = Reports()
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        exit_code = int(pytest.main(["-q", *EXPECTED], plugins=[observer]))
    accepted = matches(exit_code, observer.collected, observer.reports)
    test_root = Path(__file__).resolve().parent
    test_sources = [test_root / name for name in
                    ("test_boundaries.py", "verify_boundary_red.py", "test_cli.py")]
    test_sources += [FIXTURES / "error-contracts.json", *sorted(FIXTURES.glob("boundary-*.json"))]
    source_hashes = {str(path.resolve().relative_to(test_root.parents[1])):
                     hashlib.sha256(path.read_bytes()).hexdigest() for path in test_sources}
    args.diagnostics_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.diagnostics_directory,
                                     prefix="boundary-red-", suffix=".json", delete=False) as stream:
        json.dump({"pytest_exit_code": exit_code, "red_matches": accepted,
                   "test_sources_sha256": source_hashes,
                   "expected_cases": CASES, "collected": observer.collected,
                   "reports": observer.reports, "pytest_output": output.getvalue()}, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(MARKER if accepted else "RED evidence mismatch")
    return 1 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
