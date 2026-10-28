"""Real standalone reader acceptance: qualified missing-operation RED and permanent GREEN."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from generic_checks import APP, LOG, PY
from reader_checks import GROUPS
import reader_support
from reader_support import FIX, VECTORS, OBSERVATIONS, Reader, exact, load, receipt_id
from session_support import CALLS, OBSERVATIONS as SESSION_OBSERVATIONS
from state_sql import SQL_LOG


DIAGNOSTICS = []


class MissingReader(AssertionError):
    """Only the fully qualified missing-operation observation is expected RED."""


def outcome(error, suite):
    if isinstance(error, MissingReader) or (suite == 'behavior' and isinstance(error, AssertionError)):
        return 'failures'
    return 'errors'


def consumer_diagnostics():
    data = load(FIX / 'setup.json')['consumer_diagnostics']
    consumer = Reader.__new__(Reader)
    consumer.root, consumer.ids = Path('/reader-diagnostic'), {'r1': 'received-ID'}
    exact(consumer.resolve(data['binding_input']), data['binding_expected'])
    exact(consumer.resolve('${r1}', 'receipt_id'), 'received-ID')
    exact(consumer.resolve('${r1}', 'text'), '${r1}')
    exact(consumer.resolve('${missing}', 'reason'), '${missing}')
    try:
        consumer.resolve('${missing}', 'receipt_id')
    except KeyError:
        pass
    else:
        raise RuntimeError('Unreceived ID was silently accepted by test consumer')
    for value in data['valid_receipt_ids']:
        exact(receipt_id(value), value)
    for value in data['invalid_receipt_ids']:
        try:
            receipt_id(value)
        except AssertionError:
            pass
        else:
            raise RuntimeError('Test consumer accepted invalid receipt ID')
    try:
        exact('received-ID', consumer.resolve('${r1}', 'text'))
    except AssertionError:
        pass
    else:
        raise RuntimeError('Binding concealed wrong meaningful returned text')
    DIAGNOSTICS.append({'id': 'binding-and-ID-contract', 'status': 'passed',
                        'actual_binding': consumer.resolve(data['binding_input'])})
    original_observe, original_rows, original_exit = reader_support.observe_process, Reader.rows, Reader.__exit__
    # Inject only into test-owned observations after the actual standalone process ran.
    # No product implementation, state schema or producer response is mocked.
    for name in ('stderr', 'state', 'cleanup'):
        observation = {'id': 'delivery-diagnostic-' + name, 'injected': False}
        DIAGNOSTICS.append(observation)
        def observed(*arguments, **keywords):
            child, record = original_observe(*arguments, **keywords)
            observation['actual_process'] = {'exit': child.returncode,
                'stdout': child.stdout.decode('utf-8'), 'stderr': child.stderr.decode('utf-8')}
            if name == 'stderr':
                observation['injected'] = True
                child = subprocess.CompletedProcess(child.args, child.returncode,
                    child.stdout, data['stderr'].encode('utf-8'))
            return child, record
        calls = 0
        def rows(app):
            nonlocal calls
            value = original_rows(app)
            calls += 1
            if name == 'state' and calls == 3:
                observation['injected'] = True
                value['state'].append(['diagnostic-only-row', '{}'])
            return value
        def cleanup(app, *arguments):
            original_exit(app, *arguments)
            if name == 'cleanup':
                observation['injected'] = True
                raise AssertionError('Diagnostic test-owned cleanup mismatch')
        with patch('reader_support.observe_process', observed), patch.object(Reader, 'rows', rows), patch.object(Reader, '__exit__', cleanup):
            try:
                delivery()
            except Exception as error:
                observation.update(error=type(error).__name__, reason=str(error),
                                   outcome=outcome(error, 'delivery'))
                if not observation['injected'] or observation['outcome'] != 'errors':
                    raise RuntimeError('Diagnostic mismatch counted as intended reader RED') from error
            else:
                raise RuntimeError('Delivery ignored diagnostic mismatch')
        observation['status'] = 'passed'

def prerequisites():
    assert sys.version_info >= (3, 13), sys.version
    assert (APP / 'bin/poise').is_file() and (APP / 'bin/poise-state').is_file()
    data = load(VECTORS)
    exact(len(data['cases']), 21)
    exact(sum('request' in step for case in data['cases'] for step in case['steps']), 48)
    # Exercise the independent consumer's meaningful scalar type sensitivity.
    for wrong, right in [(True, 1), (1.0, 1), (1, True), ('2', 2)]:
        try:
            exact(wrong, right)
        except AssertionError:
            pass
        else:
            raise RuntimeError('Typed reader comparator accepted unequal JSON scalar types')
    consumer_diagnostics()
    return {'python': PY, 'version': sys.version, 'literal_vector_cases': 21,
            'literal_vector_requests': 48, 'supplemental_cases': len(load(FIX / 'cases.json')['cases'])}


def delivery():
    # Setup failures are diagnostic ERROR; only the exact operation refusal is intended RED.
    try:
        app = Reader().__enter__()
    except Exception as error:
        raise RuntimeError('State/generated Session qualification failed before reader request') from error
    try:
        before = app.rows()
        code, response = app.call({'schema': 'stride-poise/v1', 'operation': 'reader.context', 'input': {}})
        data = load(FIX / 'setup.json')
        if code == 2 and response == data['missing_operation']:
            exact(response, data['missing_operation'])
            exact(app.rows(), before)
            raise MissingReader('Missing standalone reader.context after valid state/generated Session preparation')
        try:
            exact(code, 0)
            exact(response, data['context_response'])
            rows = app.rows()
            assert [row[0] for row in rows['state']] == [app.identity], rows
            for key in ('sessions', 'bindings', 'roles', 'hooks', 'events', 'cursors'):
                exact(rows[key], before[key])
        except Exception as error:
            raise RuntimeError('Reader entry returned neither exact qualified RED nor positive context') from error
    finally:
        app.__exit__(*sys.exc_info())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=['delivery', 'behavior'])
    parser.add_argument('--output-root')
    arguments = parser.parse_args()
    output = Path(arguments.output_root or os.environ['POISE_RUN_OUTPUT_DIR']).absolute()
    output.mkdir(parents=True, exist_ok=True)
    cases = output / 'reader-cases'
    cases.mkdir(exist_ok=True)
    tempfile.tempdir = str(cases)  # Test process only; no native caller/environment override.
    report = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=APP,
              text=True).strip(), 'suite': arguments.suite, 'python': PY, 'prerequisites': None,
              'groups': [], 'errors': [], 'failures': [], 'calls': CALLS, 'state_setup_calls': LOG,
              'sql': SQL_LOG, 'observations': OBSERVATIONS,
              'session_observations': SESSION_OBSERVATIONS, 'consumer_diagnostics': DIAGNOSTICS,
              'future_semantic_groups': [name for name, _ in GROUPS] if arguments.suite == 'delivery' else [],
              'semantic_groups_executed': 0}
    phase, executed = 'prerequisites', 0
    try:
        report['prerequisites'] = prerequisites()
        checks = [('delivery', delivery)] if arguments.suite == 'delivery' else GROUPS
        for phase, check in checks:
            executed += 1
            if arguments.suite == 'behavior':
                report['semantic_groups_executed'] += 1
            check()
            report['groups'].append({'id': phase, 'status': 'passed'})
    except Exception as error:
        report[outcome(error, arguments.suite)].append(
            {'group': phase, 'reason': type(error).__name__ + ': ' + str(error)})
    report['status'] = 'error' if report['errors'] else 'failed' if report['failures'] else 'passed'
    (output / 'reader-details.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'groups': 1 if arguments.suite == 'delivery' else 4,
                      'executed': executed, 'errors': len(report['errors']), 'failures': len(report['failures'])}))
    return 2 if report['errors'] else 1 if report['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
