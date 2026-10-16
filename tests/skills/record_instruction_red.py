"""One pytest invocation, unchanged raw diagnostics, deterministic actual events.

The registered Poise runner owns execution, output placement and process observation.
This test reporter neither schedules nor retries tests and never changes their result.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import sys

import pytest


def canonical(exit_code, collection_errors, reports):
    return json.dumps({'exit_code': exit_code,
                       'collection_errors': sorted(collection_errors),
                       'reports': sorted(reports, key=lambda v: (v['nodeid'], v['phase']))},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'


def save_line(stream, record):
    stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    stream.flush()


class Reporter:
    def __init__(self, events):
        self.events = events
        self.reports = []
        self.collection_errors = []

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(report.nodeid)
        save_line(self.events, {'event': 'collection', 'nodeid': report.nodeid,
                               'outcome': report.outcome, 'longrepr': report.longreprtext})

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        exception = None if call.excinfo is None else call.excinfo.type.__name__
        code = None
        if call.excinfo is not None:
            actual = call.excinfo.value
            if exception == 'PreparationUnavailable' and getattr(actual, 'code', None) == 'PREPARE_UNSUPPORTED':
                code = 'PREPARE_UNSUPPORTED'
            elif exception == 'PlacementUnavailable' and getattr(actual, 'code', None) == 'PLACEMENT_UNSUPPORTED':
                code = 'PLACEMENT_UNSUPPORTED'
            elif exception == 'DomainError' and str(actual) == 'Every catalog skill must be selected or explicitly excluded':
                code = 'CATALOGUE_COVERAGE'
        row = {'nodeid': report.nodeid, 'phase': report.when, 'outcome': report.outcome,
               'exception': exception, 'code': code}
        self.reports.append(row)
        save_line(self.events, {**row, 'longrepr': report.longreprtext,
                               'stdout': report.capstdout, 'stderr': report.capstderr,
                               'exception_message': None if call.excinfo is None else str(call.excinfo.value)})


def run(nodes, output_root, group):
    """Return the real pytest exit with its exact streams in runner-owned files."""
    # A script entry puts tests/skills on sys.path, unlike `python -m pytest`.
    # Bind test imports to this checkout; Poise stays bound by PYTHONPATH=src.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    output_root.mkdir(parents=True, exist_ok=True)
    with (output_root / (group + '.pytest.stdout.txt')).open('w', encoding='utf-8', newline='') as stdout, \
         (output_root / (group + '.pytest.stderr.txt')).open('w', encoding='utf-8', newline='') as stderr, \
         (output_root / (group + '.pytest.events.jsonl')).open('w', encoding='utf-8', newline='') as events:
        reporter = Reporter(events)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            actual = int(pytest.main(['-q', '-p', 'no:cacheprovider',
                                     '--rootdir', str(Path(__file__).resolve().parents[2]),
                                     '--basetemp', str(output_root / 'pytest-temp'), *nodes],
                                    plugins=[reporter]))
    return actual, canonical(actual, reporter.collection_errors, reporter.reports)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['general', 'policy'], required=True)
    args = parser.parse_args()
    root = Path(os.environ['POISE_RUN_OUTPUT_DIR'])
    # Test-owned input names; these are not derived from production policy/output.
    manifest = Path(__file__).parent / 'fixtures/general_instructions/red-groups.json'
    nodes = json.loads(manifest.read_bytes().decode('utf-8'))[args.group]
    actual, output = run(nodes, root, args.group)
    sys.stdout.write(output)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
