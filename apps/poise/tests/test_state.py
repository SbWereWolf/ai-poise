"""External acceptance checks for Poise-owned state preparation and migration."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from generic_checks import APP, LOG
from state_checks import GROUPS, Installation, prerequisites
from state_sql import SQL_LOG


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=['delivery', 'behavior'])
    parser.add_argument('--output-root')
    args = parser.parse_args()
    output = Path(args.output_root or os.environ['POISE_RUN_OUTPUT_DIR']).absolute()
    report = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'],
              cwd=APP, text=True).strip(), 'suite': args.suite, 'prerequisites': None,
              'groups': [], 'errors': [], 'failures': [], 'calls': LOG, 'sql': SQL_LOG}
    phase = 'prerequisites'
    try:
        report['prerequisites'] = prerequisites()
        if args.suite == 'delivery':
            phase = 'delivery'
            with Installation() as app:
                assert (app.install / 'bin/poise-state').is_file(), 'Missing standalone state entry bin/poise-state'
            report['groups'].append({'id': 'delivery', 'status': 'passed'})
        else:
            for name, check in GROUPS:
                phase = name
                check()
                report['groups'].append({'id': name, 'status': 'passed'})
    except AssertionError as exc:
        report['failures'].append({'group': phase, 'reason': str(exc)})
    except Exception as exc:
        report['errors'].append({'group': phase, 'reason': type(exc).__name__ + ': ' + str(exc)})
    status = 'error' if report['errors'] else 'failed' if report['failures'] else 'passed'
    report['status'] = status
    report['semantic_groups_executed'] = len(report['groups']) if args.suite == 'behavior' else 0
    report['future_semantic_groups'] = [name for name, _ in GROUPS] if args.suite == 'delivery' else []
    output.mkdir(parents=True, exist_ok=True)
    (output / 'state-details.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': status, 'groups': 1 if args.suite == 'delivery' else len(GROUPS),
                      'executed': len(report['groups']) + len(report['failures']),
                      'errors': len(report['errors']), 'failures': len(report['failures'])}))
    return 2 if report['errors'] else 1 if report['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
