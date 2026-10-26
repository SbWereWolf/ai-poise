"""F01: actual unavailable preparation differs from an evaluated graph failure."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

from graph_acceptance_checks import Installation, LOG, ROOT, prerequisites, read, run_case, write


def preparation_refusal(report):
    with Installation() as case:
        case.arrange('full')
        before = deepcopy(case.plan)
        case.plan['execution_config']['read']['max_queries'] = 0
        write(case.plan_path, case.plan)
        code, response, protocol = case.run()
        result = read(protocol['steps'][0]['outputs']['result']['path'])
        calls_path = case.workspace / 'graph-case/case' / case.plan['observation_file']
        calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
        # Retain complete facts before any assertion and before temporary cleanup.
        report['observed'] = {'original_plan': before, 'selected_plan': case.plan,
                              'selected_graph': read(case.graph_path), 'exit_code': code,
                              'response': response, 'case_result': result,
                              'protocol': protocol, 'actual_case_calls': calls}
        assert code == 3 and response['verdict'] == 'BLOCKED', (
            'Expected unavailable preparation BLOCKED/exit3; actual', code, response['verdict'])
        assert result['status'] == 'BLOCKED' and 'read.max_queries' in result['reason'], result
        assert response['graph_results'][0]['verdict'] == 'BLOCKED', response
        assert any(call['exit_code'] == 2 and 'read.max_queries' in call['stdout'] for call in calls), calls
        mutations = {'apply', 'lifecycle', 'gate', 'attempt', 'advance'}
        assert not any(call['exit_code'] == 0 and (
            call['input']['operation'] in mutations or
            (call['input']['operation'] == 'process' and
             call['input']['input']['action'] in {'create', 'edit', 'reconcile'})) for call in calls)
        checks = protocol['steps'][0]['graph']['checks']
        assert checks and all(check['status'] == 'BLOCKED' for check in checks)
        assert any(Path(item['path']) == calls_path for check in checks for item in check['evidence_paths']), (
            'Actual refusal calls must remain referenced graph evidence', checks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=['behavior'])
    parser.add_argument('--with-controls', action='store_true')
    parser.add_argument('--as-root', action='store_true')
    parser.add_argument('--output-root')
    args = parser.parse_args()
    output = Path(args.output_root or os.environ['POISE_RUN_OUTPUT_DIR']).absolute()
    if os.geteuid() != 0:
        if args.as_root:
            raise RuntimeError('Explicit Ubuntu root entry did not provide its declared identity')
        argv = ['/mnt/c/Windows/System32/wsl.exe', '--distribution', 'Ubuntu', '--user', 'root', '--',
                sys.executable, '-B', str(Path(__file__).absolute()), '--suite', args.suite,
                '--as-root', '--output-root', str(output)]
        if args.with_controls:
            argv.append('--with-controls')
        result = subprocess.run(argv, capture_output=True, check=False)
        sys.stdout.buffer.write(result.stdout)
        sys.stderr.buffer.write(result.stderr)
        return result.returncode
    report = {'source_commit': subprocess.check_output([
        'git', '-c', 'safe.directory=' + str(ROOT), '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
        'prerequisites': None, 'controls': [], 'observed': None, 'errors': [], 'failures': [], 'logs': LOG}
    phase = 'prerequisites'
    try:
        report['prerequisites'] = prerequisites()
        if args.with_controls:
            phase = 'controls'
            for scenario, false_effect in [('full', False), ('template', False), ('edited', False),
                                           ('invalid', False), ('full', True)]:
                report['controls'].append(run_case(scenario, false_effect=false_effect))
        phase = 'preparation_refusal'
        preparation_refusal(report)
    except AssertionError as exc:
        report['failures'].append({'phase': phase, 'reason': str(exc)})
    except Exception as exc:
        report['errors'].append({'phase': phase, 'reason': type(exc).__name__ + ': ' + str(exc)})
    status = 'error' if report['errors'] else 'failed' if report['failures'] else 'passed'
    report['status'] = status
    output.mkdir(parents=True, exist_ok=True)
    (output / 'preparation-details.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': status, 'checks': 1, 'controls': len(report['controls']),
                      'errors': len(report['errors']), 'failures': len(report['failures'])}))
    return 2 if report['errors'] else 1 if report['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
