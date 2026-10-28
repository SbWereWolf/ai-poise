"""Qualified missing-capability RED and the same permanent positive CLI checks."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import fs_support
from generic_checks import APP, LOG, PY
from fs_checks import GROUPS
from fs_support import DiagnosticError, MissingPolicy, Files, OBSERVATIONS, load
from reader_support import exact
from session_support import CALLS
from state_sql import SQL_LOG


DIAGNOSTICS = []


def outcome(error, suite):
    if isinstance(error, MissingPolicy) or suite == 'behavior' and isinstance(error, AssertionError):
        return 'failures'
    return 'errors'


def prerequisites():
    for wrong, right in ((True, 1), (1.0, 1), (1, True)):
        try:
            exact(wrong, right)
        except AssertionError:
            pass
        else:
            raise DiagnosticError('Independent comparator ignored scalar types')
    consumer = Files.__new__(Files)
    consumer.root, consumer.ids = Path('/fixture-consumer'), {'r1': 'received-id'}
    values = load('setup.json')['binding_diagnostics']
    for control in values:
        exact(consumer.bind(control['input'], scope=control['scope']), control['expected'],
              'binding/' + control['id'])
    try:
        consumer.bind({'schema': 'stride-poise/v1', 'operation': 'reader.read',
                       'input': {'acknowledged': ['${missing}']}}, scope='request')
    except KeyError:
        pass
    else:
        raise DiagnosticError('Unknown receipt reference was accepted')
    for error in (DiagnosticError('stderr'), DiagnosticError('state'), DiagnosticError('cleanup'),
                  AssertionError('unqualified assertion')):
        exact(outcome(error, 'delivery'), 'errors')
    try:
        with Files(reader=True) as app:
            setup = load('setup.json')
            app.step(setup['reader_context'])
            direct = deepcopy(setup['observer_control'])
            direct['observed'] = False
            direct.pop('opens')
            first, _ = app.step(direct)
            second, record = app.step(setup['observer_control'])
            exact(first, second)
            exact(len(record['target_opens']), 1)
            DIAGNOSTICS.append({'id': 'actual-reader-direct-observed-control', 'status': 'passed',
                                'target_opens': record['target_opens']})
    except Exception as error:
        raise DiagnosticError('Actual copied-reader/observer qualification failed') from error
    state_and_config_controls()
    consumer_diagnostics()
    return {'python': PY, 'version': sys.version, 'groups_defined': [name for name, _ in GROUPS],
            'literal_cases': {name: len(value) for name, value in load('cases.json')['groups'].items()}}


def delivery():
    setup = load('setup.json')
    missing = []
    try:
        with Files() as app:
            for spec in setup['delivery']:
                code, response, record = app.call(spec['request'])
                if code == 2 and response == setup['missing_operation']:
                    exact(response, setup['missing_operation'])
                    exact(record['after'], record['before'])
                    exact(record['target_opens'], [])
                    record['qualified_missing_dispatch'] = True
                    missing.append(spec['request']['operation'])
                    continue
                exact(code, spec['expected_exit'])
                exact(response, app.bind(spec['expected_response'], scope='response'))
                if spec['request']['operation'] == 'paths.evaluate':
                    exact(record['after'], record['before'])
                    exact(record['target_opens'], [])
                for name, text in spec.get('files', {}).items():
                    exact(app.path(name).read_bytes(), text.encode('utf-8'))
            app.record['missing_operations'] = missing
    except Exception as error:
        raise DiagnosticError('Delivery returned neither qualified missing dispatch nor exact positive effect') from error
    if missing:
        raise MissingPolicy('Missing standalone operations after valid copied-app/config preparation: '
                            + ', '.join(missing))


def state_and_config_controls():
    setup = load('setup.json')
    cases = load('cases.json')['groups']
    json_case = next(case for case in cases['writer'] if case['id'] == 'later-invalid-text')
    with Files() as app:
        app.step(json_case['steps'][0])
        DIAGNOSTICS.append({'id': 'actual-strict-json-surrogate-boundary', 'status': 'passed'})
    with Files(reader=True) as app:
        options = deepcopy(setup['opaque_config_control'])
        app.session.config.update(options)
        exact({key: app.session.config[key] for key in options}, options)
        _, record = app.step(setup['reader_context'])
        written = json.loads(bytes.fromhex(record['config_hex']))
        exact({key: written[key] for key in options}, options)
        DIAGNOSTICS.append({'id': 'actual-opaque-config-byte-retention', 'status': 'passed'})
    for control in setup['contradictory_state_controls']:
        with Files(reader=True) as app:
            app.step(setup['reader_context'])
            spec = deepcopy(setup['observer_control'])
            spec['effects'] = control['effects']
            try:
                app.step(spec)
            except AssertionError as error:
                if control['failure_label'] not in str(error):
                    raise DiagnosticError('State control failed before its intended assertion') from error
                DIAGNOSTICS.append({'id': control['id'], 'status': 'rejected_wrong_expectation',
                                    'reason': str(error)})
            else:
                raise DiagnosticError('Contradictory state expectation was silently ignored')
    # Positive controls exercise all six existing nonempty effect declarations.
    effect_steps = 0
    for case in cases['reader']:
        if not any(step.get('effects') for step in case['steps']):
            continue
        with Files(reader=True) as app:
            app.session.config['paths'] = app.bind(case['policy'], scope='policy')
            for document in case.get('documents', []):
                app.document(document)
            app.step(setup['reader_context'])
            for spec in case['steps']:
                app.step(spec)
                effect_steps += bool(spec.get('effects'))
    exact(effect_steps, setup['state_effect_steps'])
    DIAGNOSTICS.append({'id': 'actual-positive-reader-state-oracles', 'status': 'passed',
                        'effect_steps': effect_steps})


def consumer_diagnostics():
    original_observe, original_facts, original_exit = (fs_support.observe_process,
                                                       Files.facts, Files.__exit__)
    for name in ('stderr', 'state', 'cleanup'):
        observation = {'id': 'delivery-diagnostic-' + name, 'injected': False}
        DIAGNOSTICS.append(observation)

        def observed(*args, **kwargs):
            child, record = original_observe(*args, **kwargs)
            record['test_diagnostic'] = name
            if name == 'stderr':
                observation['injected'] = True
                child = subprocess.CompletedProcess(child.args, child.returncode,
                                                     child.stdout, b'test-owned diagnostic\n')
            return child, record

        calls = 0

        def facts(app):
            nonlocal calls
            result = original_facts(app)
            calls += 1
            if name == 'state' and calls == 2:
                observation['injected'] = True
                result['test-owned-diagnostic-only'] = 'ff'
            return result

        def cleanup(app, *args):
            original_exit(app, *args)
            if name == 'cleanup':
                observation['injected'] = True
                raise AssertionError('Injected test-owned cleanup discrepancy')

        with patch('fs_support.observe_process', observed), patch.object(Files, 'facts', facts), \
                patch.object(Files, '__exit__', cleanup):
            try:
                delivery()
            except DiagnosticError as error:
                exact(outcome(error, 'delivery'), 'errors')
                exact(observation['injected'], True)
                observation['classification'] = 'errors'
            else:
                raise DiagnosticError('Actual delivery ignored its test-owned diagnostic discrepancy')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=['delivery', 'behavior'])
    parser.add_argument('--output-root')
    args = parser.parse_args()
    output = Path(args.output_root or os.environ['POISE_RUN_OUTPUT_DIR']).absolute()
    output.mkdir(parents=True, exist_ok=True)
    cases = output / 'fs-cases'
    cases.mkdir(exist_ok=True)
    tempfile.tempdir = str(cases)
    report = {'suite': args.suite, 'source_commit': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=APP, text=True).strip(), 'python': PY,
        'prerequisites': None, 'groups': [], 'errors': [], 'failures': [],
        'observations': OBSERVATIONS, 'calls': CALLS, 'setup_calls': LOG, 'sql': SQL_LOG,
        'consumer_diagnostics': DIAGNOSTICS,
        'future_semantic_groups': [name for name, _ in GROUPS] if args.suite == 'delivery' else [],
        'semantic_groups_executed': 0}
    phase, executed = 'prerequisites', 0
    try:
        report['prerequisites'] = prerequisites()
        checks = [('delivery', delivery)] if args.suite == 'delivery' else GROUPS
        for phase, check in checks:
            executed += 1
            if args.suite == 'behavior':
                report['semantic_groups_executed'] += 1
            check()
            report['groups'].append({'id': phase, 'status': 'passed'})
    except Exception as error:
        report[outcome(error, args.suite)].append({'group': phase,
            'reason': type(error).__name__ + ': ' + str(error)})
    report['status'] = 'error' if report['errors'] else 'failed' if report['failures'] else 'passed'
    (output / 'fs-details.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'groups': 1 if args.suite == 'delivery' else 4,
                      'executed': executed, 'errors': len(report['errors']),
                      'failures': len(report['failures'])}))
    return 2 if report['errors'] else 1 if report['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
