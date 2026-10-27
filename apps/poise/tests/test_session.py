"""External acceptance runner with an honest legacy-operation RED."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from generic_checks import APP, LOG, PY, bind
from session_checks import GROUPS
from session_qualification import prerequisites
from session_support import CALLS, OBSERVATIONS, Installation, fixture, observe_process
from state_sql import SQL_LOG


def delivery():
    with Installation() as app:
        try:
            app.prepare()
        except Exception as error:
            raise RuntimeError('Accepted state setup failed before Session RED') from error
        legacy = bind(fixture('legacy-red-config.json'), {
            '@STATE_ROOT@': str(app.database.parent), '@DATABASE@': str(app.database),
            '@LOCK@': str(app.root / 'unused.lock'), '@RESULTS@': str(app.root / 'results')})
        raw_config = (json.dumps(legacy, ensure_ascii=False, indent=2) + '\n').encode()
        app.path.write_bytes(raw_config)
        raw = json.dumps(app.packet('session.ensure', {})) + '\n'
        child, record = observe_process([PY, '-I', '-S', '-B', app.install / 'bin/poise',
                                         '--config', app.path], raw, app.state.cwd, app.environment())
        record['config_raw'] = raw_config.decode()
        try:
            body = json.loads(child.stdout.decode('utf-8'))
        except (ValueError, UnicodeError) as error:
            raise RuntimeError('Legacy entry did not reach the intended operation refusal') from error
        record['decoded'] = body
        if (child.returncode != 2 or child.stderr
                or body != fixture('legacy-red-response.json')):
            raise RuntimeError('Legacy config/entry did not yield exact /operation refusal; not product RED')
        assert child.returncode == 0 and body['status'] == 'ok', (
            'Missing standalone session.ensure: old entry rejected /operation after valid legacy config')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=['delivery', 'behavior'])
    parser.add_argument('--output-root')
    args = parser.parse_args()
    output = Path(args.output_root or os.environ['POISE_RUN_OUTPUT_DIR'])
    report = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=APP,
              text=True).strip(), 'suite': args.suite, 'prerequisites': None, 'groups': [],
              'errors': [], 'failures': [], 'calls': CALLS, 'state_setup_calls': LOG,
              'sql': SQL_LOG, 'observations': OBSERVATIONS,
              'future_semantic_groups': [name for name, _ in GROUPS] if args.suite == 'delivery' else [],
              'semantic_groups_executed': 0}
    phase, attempted = 'prerequisites', 0
    try:
        report['prerequisites'] = prerequisites()
        checks = [('delivery', delivery)] if args.suite == 'delivery' else GROUPS
        for phase, check in checks:
            attempted += 1
            if args.suite == 'behavior':
                report['semantic_groups_executed'] += 1
            check()
            report['groups'].append({'id': phase, 'status': 'passed'})
    except AssertionError as error:
        report['failures'].append({'group': phase, 'reason': str(error)})
    except Exception as error:
        report['errors'].append({'group': phase, 'reason': type(error).__name__ + ': ' + str(error)})
    report['status'] = 'error' if report['errors'] else 'failed' if report['failures'] else 'passed'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'session-details.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'groups': 1 if args.suite == 'delivery' else 6,
                      'executed': attempted, 'errors': len(report['errors']),
                      'failures': len(report['failures'])}))
    return 2 if report['errors'] else 1 if report['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
