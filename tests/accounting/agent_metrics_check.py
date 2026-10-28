"""Standard pytest + existing complete lifecycle reporter; Poise owns execution."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path

import pytest

from tests.actions.lifecycle_reporting import LifecycleReport


def main():
    configuration = Path(__file__).parent / "fixtures/agent_metrics/check-groups.json"
    groups = json.loads(configuration.read_text())
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=tuple(groups), required=True)
    arguments = parser.parse_args()
    output = Path(os.environ["POISE_RUN_OUTPUT_DIR"])
    assert output.is_absolute() and output.is_dir(), "F16_EXPLICIT_CHECK_OUTPUT_REQUIRED"
    runtime = output / "pytest-runtime"
    assert not runtime.exists(), "F16_CHECK_REFUSES_TO_ERASE_EXISTING_RUNTIME"
    reporter = LifecycleReport()
    selectors = groups[arguments.group]
    with (output / "pytest.stdout.txt").open("w", encoding="utf-8", buffering=1) as stdout:
        with (output / "pytest.stderr.txt").open("w", encoding="utf-8", buffering=1) as stderr:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = int(pytest.main(["-q", "--tb=short", "--basetemp", str(runtime), *selectors],
                                       plugins=[reporter]))
    actual = reporter.summary(code)
    encoded = json.dumps(actual, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    (output / "phases.json").write_text(encoded + "\n")
    print(encoded)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
