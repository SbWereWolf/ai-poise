"""Independent hook acceptance runner: genuine missing-operation RED first."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from generic_checks import APP, LOG
from hook_event_checks import native_lifecycle, repeat_isolation, boundaries_atomic, retained_standalone
from hook_intercept_checks import public_call, advisory_probes
import hook_support
from hook_support import Installation, equal, fixture
from session_qualification import prerequisites
from session_support import CALLS, OBSERVATIONS
from state_sql import SQL_LOG

GROUPS = [('native-lifecycle', native_lifecycle), ('repeat-isolation', repeat_isolation),
          ('public-call', public_call), ('advisory-probes', advisory_probes),
          ('boundaries-atomic', boundaries_atomic), ('retained-standalone', retained_standalone)]


def delivery():
    with Installation() as app:
        try:
            app.prepare()
            before = app.facts()
            child, record, body = app.call('hook.event', app.event_input())
            equal(body, fixture('old-refusal.json'))
            equal(app.facts(), before)
            if child.returncode != 2 or child.stderr:
                raise RuntimeError('Current entry did not yield exact operation refusal')
        except Exception as error:
            raise RuntimeError('Hook entry/setup qualification failed; this is not product RED') from error
        assert child.returncode == 0 and body['status'] == 'ok', (
            'Missing standalone hook.event: existing valid entry rejects /operation; own state unchanged')


def documentation_checks():
    """Four structural signals; semantic reading and real examples remain DOC work."""
    docs = APP / 'docs'
    texts = {str(path.relative_to(docs)): path.read_text() for path in docs.rglob('*.md')}
    hooks = [value for value in texts.values() if 'hook.event' in value and 'hook.intercept' in value]
    assert hooks, 'Current operator documentation must describe both public hook operations'
    own = '\n'.join(hooks)
    checks = [
        ('public-input-output', lambda: all(token in own for token in
          ['event_id', 'workspace', 'request', 'native_response', 'capability_checks'])),
        ('configured-operation', lambda: all(token in own for token in
          ['generic_config', 'external', 'probes', '--config'])),
        ('own-state-and-boundaries', lambda: all(token in own for token in
          ['SessionStart', 'UserPromptSubmit', 'SessionEnd', 'hook_not_found', 'external_not_configured'])),
        ('complete-observations', lambda: all(token in own for token in
          ['provider_timeout', 'external_response', 'stdout', 'stderr', 'receipt']))]
    for identity, check in checks:
        yield identity, check


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=['delivery', 'behavior', 'documentation'])
    parser.add_argument('--output-root')
    args = parser.parse_args()
    output = Path(args.output_root or os.environ['POISE_RUN_OUTPUT_DIR'])
    output.mkdir(parents=True, exist_ok=True)
    report = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=APP,
              text=True).strip(), 'suite': args.suite, 'prerequisites': None, 'groups': [],
              'errors': [], 'failures': [], 'calls': CALLS, 'state_setup_calls': LOG,
              'sql': SQL_LOG, 'observations': OBSERVATIONS,
              'future_semantic_groups': [name for name, _ in GROUPS] if args.suite == 'delivery' else [],
              'semantic_groups_executed': 0}
    def persist():
        (output / 'hook-details.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    hook_support.CHECKPOINT = persist
    phase, attempted = 'prerequisites', 0
    count = {'delivery': 1, 'behavior': 6, 'documentation': 4}[args.suite]
    try:
        report['prerequisites'] = prerequisites()
        checks = [('delivery', delivery)] if args.suite == 'delivery' else GROUPS
        if args.suite == 'documentation':
            checks = documentation_checks()
        for phase, check in checks:
            attempted += 1
            if args.suite == 'behavior':
                report['semantic_groups_executed'] += 1
            result = check()
            if args.suite == 'documentation':
                assert result is True, phase
            report['groups'].append({'id': phase, 'status': 'passed'})
    except AssertionError as error:
        report['failures'].append({'group': phase, 'reason': str(error)})
    except Exception as error:
        report['errors'].append({'group': phase, 'reason': type(error).__name__ + ': ' + str(error)})
    finally:
        report['status'] = 'error' if report['errors'] else 'failed' if report['failures'] else 'passed'
        persist()
    print(json.dumps({'status': report['status'], 'groups': count, 'executed': attempted,
                      'errors': len(report['errors']), 'failures': len(report['failures'])}))
    return 2 if report['errors'] else 1 if report['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
