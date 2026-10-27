"""Independent external checks of standalone generic capability extraction."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

from generic_checks import APP, GROUPS, Installation, LOG, prerequisites


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=['delivery', 'behavior'])
    parser.add_argument('--output-root')
    args = parser.parse_args()
    output = Path(args.output_root or os.environ['POISE_RUN_OUTPUT_DIR']).absolute()
    report = {'source_commit': subprocess.check_output([
        'git', 'rev-parse', 'HEAD'], cwd=APP, text=True).strip(),
        'suite': args.suite, 'prerequisites': None, 'groups': [],
        'errors': [], 'failures': [], 'calls': LOG}
    phase = 'prerequisites'
    try:
        report['prerequisites'] = prerequisites()
        if args.suite == 'delivery':
            phase = 'delivery'
            with Installation() as installation:
                assert (installation.install / 'bin/poise-generic').is_file(), 'Missing standalone generic command bin/poise-generic'
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
    output.mkdir(parents=True, exist_ok=True)
    (output / 'generic-details.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    total = 1 if args.suite == 'delivery' else len(GROUPS)
    print(json.dumps({'status': status, 'groups': total,
                      'executed': len(report['groups']) + len(report['failures']),
                      'errors': len(report['errors']), 'failures': len(report['failures'])}))
    return 2 if report['errors'] else 1 if report['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
