"""Independent tests of delivered real graph cases; no product imports."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from graph_acceptance_checks import CASE, GROUPS, LOG, ROOT, prerequisites, run_case


def test_full_graph():
    return run_case('full')


def test_template_graph():
    return run_case('template')


def test_edited_graph():
    return run_case('edited')


def test_invalid_graph():
    return run_case('invalid')


def test_false_effect():
    return run_case('full', false_effect=True)


CHECKS = [test_full_graph, test_template_graph, test_edited_graph,
          test_invalid_graph, test_false_effect]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=['behavior'])
    parser.add_argument('--as-root', action='store_true')
    parser.add_argument('--output-root')
    args = parser.parse_args()
    output = Path(args.output_root or os.environ['POISE_RUN_OUTPUT_DIR']).absolute()
    if os.geteuid() != 0:
        if args.as_root:
            raise RuntimeError('Explicit WSL root entry failed')
        argv = ['/mnt/c/Windows/System32/wsl.exe', '--distribution', 'Ubuntu', '--user', 'root', '--',
                sys.executable, '-B', str(Path(__file__).absolute()), '--suite', args.suite,
                '--as-root', '--output-root', str(output)]
        result = subprocess.run(argv, capture_output=True, check=False)
        sys.stdout.buffer.write(result.stdout)
        sys.stderr.buffer.write(result.stderr)
        return result.returncode
    report = {'source_commit': subprocess.check_output(
        ['git', '-c', 'safe.directory=' + str(ROOT), '-C', str(ROOT), 'rev-parse', 'HEAD'],
        text=True).strip(), 'prerequisites': None, 'cases': [], 'errors': [], 'failures': [],
        'unexecuted': list(GROUPS), 'logs': LOG}
    status = 'passed'
    try:
        report['prerequisites'] = prerequisites()
        if not CASE.is_file():
            # Approved named delivery RED; no missing-file process/import failure.
            status = 'missing_graph_acceptance'
            report['failures'].append({'requirement': 'delivered_real_graph_acceptance',
                                       'reason': 'App-owned acceptance case is not delivered',
                                       'path': str(CASE)})
        else:
            for name, check in zip(GROUPS, CHECKS):
                try:
                    report['cases'].append(check())
                except AssertionError as exc:
                    report['failures'].append({'case': name, 'reason': str(exc)})
                except Exception as exc:
                    report['errors'].append({'case': name, 'reason': type(exc).__name__ + ': ' + str(exc)})
                report['unexecuted'].remove(name)
    except Exception as exc:
        report['errors'].append({'phase': 'prerequisites', 'reason': type(exc).__name__ + ': ' + str(exc)})
    if report['errors']:
        status = 'error'
    elif report['failures'] and status != 'missing_graph_acceptance':
        status = 'failed'
    report['status'] = status
    output.mkdir(parents=True, exist_ok=True)
    (output / 'graph-acceptance-details.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': status, 'groups': len(GROUPS), 'executed': len(GROUPS) - len(report['unexecuted']),
                      'errors': len(report['errors']), 'failures': len(report['failures'])}))
    return 2 if report['errors'] else 1 if report['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
