"""Standalone binary output checks: qualified RED and unchanged GREEN expectations."""
import argparse
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

from output_support import (CALLS, VECTORS, OutputFixture, MissingOutput, DiagnosticError,
                            check_reads, controls, preservation_controls, encoded, exact, fault, metadata,
                            minimal, ranged, ranges, discrepancy, source_fault, RESPONSE_LIMIT)


CASES = []


def case(name, group):
    def register(function):
        CASES.append((name, group, function))
        return function
    return register


@case('minimal-two-streams', 'minimal-streams')
def minimal_known(app):
    expected = minimal(metadata(app.paths['binary'], 6, 6), metadata(app.paths['empty'], 0, 0))
    observed = app.expect('output.minimal', {'sources': [app.descriptor(), app.descriptor('empty', 0)]}, expected)
    check_reads(observed['source_reads'], app.paths['binary'])
    check_reads(observed['source_reads'], app.paths['empty'])
    exact(app.paths['binary'].read_bytes(), bytes.fromhex(VECTORS['binary_hex']))


@case('minimal-unknown-extent', 'minimal-streams')
def unknown(app):
    app.expect('output.minimal', {'sources': [app.descriptor(captured=None)]},
               minimal(metadata(app.paths['binary'], None, 6, 'unknown')))


@case('minimal-ordered-duplicates', 'batch-and-input')
def duplicates(app):
    app.expect('output.minimal', {'sources': [app.descriptor('empty', 0), app.descriptor(), app.descriptor()]},
               minimal(metadata(app.paths['empty'], 0, 0), metadata(app.paths['binary'], 6, 6),
                       metadata(app.paths['binary'], 6, 6)))


def register_vector(vector):
    @case(vector['id'], 'binary-ranges' if vector['actual'] else 'empty-and-end')
    def check(app):
        name = vector['fixture']
        size = 0 if name == 'empty' else 6
        expected = ranges(ranged(app.paths[name], size, size, vector['start'], vector['actual'],
                                 vector['base64'], vector['next'], vector['eof']))
        observed = app.expect('output.range', {'reads': [{'source': app.descriptor(name, size),
                               'start': vector['start'], 'count': vector['count']}]}, expected)
        if vector['actual']:
            check_reads(observed['source_reads'], app.paths[name], vector['start'], vector['actual'])
    return check


for vector in VECTORS['ranges']:
    register_vector(vector)


@case('range-after-end', 'empty-and-end')
def after_end(app):
    app.expect('output.range', {'reads': [{'source': app.descriptor(), 'start': 7, 'count': 1}]},
               fault('output_range_bounds', {'index': 0, 'start': 7, 'bytes': 6}, 'correct:request'))


@case('minimal-readable-truncation', 'extent-loss')
def truncated(app):
    app.paths['binary'].write_bytes(b'AB')
    app.expect('output.minimal', {'sources': [app.descriptor()]},
               discrepancy(minimal(metadata(app.paths['binary'], 6, 2, 'shorter')), [0]))


@case('range-readable-truncation', 'extent-loss')
def truncated_range(app):
    app.paths['binary'].write_bytes(b'AB')
    expected = ranges(ranged(app.paths['binary'], 6, 2, 0, 2,
                             VECTORS['literal_shortened_base64'], None, True, 'shorter'))
    app.expect('output.range', {'reads': [{'source': app.descriptor(), 'start': 0, 'count': 6}]},
               discrepancy(expected, [0]))


@case('minimal-longer-retained-file', 'extent-loss')
def longer(app):
    app.paths['binary'].write_bytes(b'ABCDEFG')
    app.expect('output.minimal', {'sources': [app.descriptor()]},
               discrepancy(minimal(metadata(app.paths['binary'], 6, 7, 'longer')), [0]))


@case('ordered-range-batch', 'batch-and-input')
def range_batch(app):
    app.config['output']['limits']['max_total_range_bytes'] = 4
    app.expect('output.range', {'reads': [
        {'source': app.descriptor('empty', 0), 'start': 0, 'count': 1},
        {'source': app.descriptor(), 'start': 1, 'count': 3}]},
        ranges(ranged(app.paths['empty'], 0, 0, 0, 0, '', None, True),
               ranged(app.paths['binary'], 6, 6, 1, 3, 'Qv8K', 4, False)))


@case('batch-real-error-no-partial-result', 'batch-and-input')
def failed_batch(app):
    missing = {'path': str(app.directory / 'absent-capture'), 'captured_bytes': 5}
    app.expect('output.minimal', {'sources': [app.descriptor(), missing]}, source_fault(1))


@case('batch-bounds-error-index', 'batch-and-input')
def bounds_batch(app):
    app.expect('output.range', {'reads': [
        {'source': app.descriptor(), 'start': 0, 'count': 1},
        {'source': app.descriptor(), 'start': 9, 'count': 1}]},
        fault('output_range_bounds', {'index': 1, 'start': 9, 'bytes': 6}, 'correct:request'))


@case('validate-entire-batch-before-read', 'batch-and-input')
def whole_validation(app):
    record = app.expect('output.range', {'reads': [
        {'source': app.descriptor(), 'start': 1, 'count': 3},
        {'source': app.descriptor(), 'start': 0, 'count': False}]},
        fault('invalid_request', {'path': '/input/reads/1/count'}, 'correct:request'))
    check_reads(record['source_reads'], app.paths['binary'])


@case('item-limit-exact', 'bounded-delivery')
def exact_items(app):
    app.config['output']['limits']['max_items'] = 1
    app.expect('output.minimal', {'sources': [app.descriptor()]}, minimal(metadata(app.paths['binary'], 6, 6)))


@case('item-limit-exceeded', 'bounded-delivery')
def excess_items(app):
    app.config['output']['limits']['max_items'] = 1
    app.expect('output.minimal', {'sources': [app.descriptor(), app.descriptor()]},
               fault('output_read_limit', {'limit': 'max_items'}, 'reduce:output'))


def byte_budget(app, exceeded):
    app.config['output']['limits']['max_total_range_bytes'] = 3
    count = 4 if exceeded else 3
    expected = (fault('output_read_limit', {'limit': 'max_total_range_bytes'}, 'reduce:output')
                if exceeded else ranges(ranged(app.paths['binary'], 6, 6, 1, 3, 'Qv8K', 4, False)))
    app.expect('output.range', {'reads': [{'source': app.descriptor(), 'start': 1, 'count': count}]}, expected)


case('byte-limit-exact', 'bounded-delivery')(lambda app: byte_budget(app, False))
case('byte-limit-exceeded', 'bounded-delivery')(lambda app: byte_budget(app, True))


def response_budget(app, delta):
    expected = minimal(metadata(app.paths['binary'], 6, 6))
    budget = len(encoded(expected)) + delta
    app.config['output']['limits']['max_response_bytes'] = budget
    record = app.expect('output.minimal', {'sources': [app.descriptor()]},
                        expected if delta == 0 else RESPONSE_LIMIT)
    if delta == 0:
        exact(len(bytes.fromhex(record['stdout_hex'])), budget)


case('response-exact-utf8-boundary', 'bounded-delivery')(lambda app: response_budget(app, 0))
case('response-one-byte-too-small', 'bounded-delivery')(lambda app: response_budget(app, -1))


@case('response-cap-one-diagnostic-exemption', 'bounded-delivery')
def cap_one(app):
    app.config['output']['limits']['max_response_bytes'] = 1
    app.expect('output.minimal', {'sources': [app.descriptor('empty', 0)]}, RESPONSE_LIMIT)


@case('source-error-before-tiny-budget', 'ordinary-failures')
def error_precedence(app):
    app.config['output']['limits']['max_response_bytes'] = 1
    app.expect('output.minimal', {'sources': [{'path': str(app.directory / ('missing-' + 'x' * 120)),
                                            'captured_bytes': 4}]}, source_fault(0))


@case('discrepancy-data-too-large', 'bounded-delivery')
def discrepancy_budget(app):
    app.paths['binary'].write_bytes(b'AB')
    app.config['output']['limits']['max_response_bytes'] = 1
    app.expect('output.minimal', {'sources': [app.descriptor()]}, RESPONSE_LIMIT)


@case('range-total-count-before-clamping', 'bounded-delivery')
def requested_sum(app):
    app.config['output']['limits']['max_total_range_bytes'] = 1
    app.expect('output.range', {'reads': [{'source': app.descriptor('empty', 0), 'start': 0, 'count': 2}]},
               fault('output_read_limit', {'limit': 'max_total_range_bytes'}, 'reduce:output'))


@case('aggregate-limit-before-source-error', 'bounded-delivery')
def aggregate_precedence(app):
    app.config['output']['limits']['max_total_range_bytes'] = 1
    descriptor = {'path': str(app.directory / 'absent-capture'), 'captured_bytes': None}
    app.expect('output.range', {'reads': [{'source': descriptor, 'start': 0, 'count': 2}]},
               fault('output_read_limit', {'limit': 'max_total_range_bytes'}, 'reduce:output'))


@case('aggregate-sum-of-individually-valid-counts', 'bounded-delivery')
def aggregate_sum(app):
    app.config['output']['limits']['max_total_range_bytes'] = 3
    app.expect('output.range', {'reads': [
        {'source': app.descriptor('empty', 0), 'start': 0, 'count': 2},
        {'source': app.descriptor(), 'start': 1, 'count': 2}]},
        fault('output_read_limit', {'limit': 'max_total_range_bytes'}, 'reduce:output'))


def invalid_value(app, variant):
    descriptor = app.descriptor()
    if variant == 'relative-path':
        descriptor['path'] = 'stdout.bin'
        request, path = {'sources': [descriptor]}, '/input/sources/0/path'
    elif variant == 'bool-capture':
        descriptor['captured_bytes'] = True
        request, path = {'sources': [descriptor]}, '/input/sources/0/captured_bytes'
    elif variant == 'missing-capture':
        descriptor.pop('captured_bytes')
        request, path = {'sources': [descriptor]}, '/input/sources/0/captured_bytes'
    elif variant == 'empty-batch':
        request, path = {'sources': []}, '/input/sources'
    elif variant == 'extra-field':
        request, path = {'sources': [descriptor], 'unknown': 1}, '/input'
    else:
        raise DiagnosticError('Unknown literal validation case')
    app.expect('output.minimal', request, fault('invalid_request', {'path': path}, 'correct:request'))


for variant in ('relative-path', 'bool-capture', 'missing-capture', 'empty-batch', 'extra-field'):
    case('input-' + variant, 'batch-and-input')(lambda app, value=variant: invalid_value(app, value))


def bad_range_number(app, field, value):
    item = {'source': app.descriptor(), 'start': 0, 'count': 1}
    item[field] = value
    app.expect('output.range', {'reads': [item]},
               fault('invalid_request', {'path': '/input/reads/0/' + field}, 'correct:request'))


for name, field, value in [('bool-start', 'start', True), ('bool-count', 'count', True),
                            ('negative-start', 'start', -1), ('zero-count', 'count', 0)]:
    case('input-' + name, 'batch-and-input')(lambda app, f=field, v=value: bad_range_number(app, f, v))


def bad_config(app, variant):
    if variant == 'missing':
        app.config.pop('output')
        expected = fault('output_not_configured', {}, 'configure:output', 'unavailable')
    else:
        if variant == 'schema':
            app.config['output']['schema'] = 'other'
            path = '/output/schema'
        else:
            app.config['output']['limits']['max_items'] = True
            path = '/output/limits/max_items'
        expected = fault('invalid_config', {'path': path}, 'correct:config')
    app.expect('output.minimal', {'sources': [app.descriptor()]}, expected)


for variant in ('missing', 'schema', 'bool-limit'):
    case('config-' + variant, 'batch-and-input')(lambda app, v=variant: bad_config(app, v))


@case('selected-output-unknown-option-preserved', 'batch-and-input')
def unknown_option(app):
    app.config['output']['operator_note'] = {'text': 'retained configuration', 'values': [1, True, None]}
    app.expect('output.minimal', {'sources': [app.descriptor()]},
               minimal(metadata(app.paths['binary'], 6, 6)))


@case('inactive-malformed-output-existing-session', 'batch-and-input')
def inactive_output(app):
    from session_support import Installation, CALLS as SESSION_CALLS, session
    first = len(SESSION_CALLS)
    with Installation() as control:
        assert control.root.is_relative_to(app.root.parent)
        control.prepare()
        control.ok('session.ensure', {'session': session(), 'created': True})
        before = control.facts()
        control.config['output'] = {'schema': False, 'limits': 'inactive malformed section'}
        raw_config = control.write_config()
        control.ok('session.status', {'session': session()})
        exact(control.path.read_bytes(), raw_config)
        exact(control.facts(), before)
    return {'kind': 'existing_operation_preservation', 'session_calls': [first, len(SESSION_CALLS)],
            'raw_config_preserved': True, 'own_database_unchanged': True}


@case('stable-fifo-no-writer', 'ordinary-failures')
def fifo(app):
    path = app.directory / 'fifo'
    os.mkfifo(path)
    app.expect('output.minimal', {'sources': [{'path': str(path), 'captured_bytes': None}]},
               source_fault(0, 'not_regular', None))


@case('directory-source', 'ordinary-failures')
def directory(app):
    app.expect('output.range', {'reads': [{'source': {'path': str(app.directory), 'captured_bytes': None},
                                         'start': 0, 'count': 1}]}, source_fault(0, 'not_regular', None))


@case('real-nonroot-eacces', 'ordinary-failures')
def eacces(app):
    app.paths['binary'].chmod(0)
    try:
        app.expect('output.minimal', {'sources': [app.descriptor()]}, source_fault(0, 'io', errno.EACCES))
    finally:
        app.paths['binary'].chmod(0o600)
        exact(app.paths['binary'].read_bytes(), bytes.fromhex(VECTORS['binary_hex']))


def large(app, representation):
    path = app.paths['binary']
    start = 16 * 1024 * 1024
    with path.open('wb') as stream:
        stream.seek(start)
        stream.write(b'AB\xff\n\0C')
    descriptor = {'path': str(path), 'captured_bytes': start + 6}
    if representation == 'minimal':
        record = app.expect('output.minimal', {'sources': [descriptor]},
                            minimal(metadata(path, start + 6, start + 6)))
        check_reads(record['source_reads'], path)
    else:
        record = app.expect('output.range', {'reads': [{'source': descriptor, 'start': start + 1, 'count': 3}]},
                            ranges(ranged(path, start + 6, start + 6, start + 1, 3, 'Qv8K', start + 4, False)))
        check_reads(record['source_reads'], path, start + 1, 3)


case('large-metadata-only', 'bounded-read-and-no-side-effects')(lambda app: large(app, 'minimal'))
case('large-seek-bounded-read', 'bounded-read-and-no-side-effects')(lambda app: large(app, 'range'))


@case('real-capture-producer-references', 'minimal-streams')
def producer(app):
    from command_support import Commands
    with Commands() as commands:
        request = commands.request('output-references')
        commands.start(request)
        terminal = commands.complete(request)
        data = terminal['result']
        descriptors = [{'path': data[name]['path'], 'captured_bytes': data[name]['bytes']}
                       for name in ('stdout', 'stderr')]
        for name, literal in [('stdout', b'OUT\n'), ('stderr', b'ERR\n')]:
            exact(Path(data[name]['path']).read_bytes(), literal)
            exact(data[name]['bytes'], 4)
        app.expect('output.minimal', {'sources': descriptors},
                   minimal(*(metadata(value['path'], 4, 4) for value in descriptors)),
                   tracked=[value['path'] for value in descriptors])


def qualify(output):
    if sys.version_info[:2] != (3, 13) or sys.platform != 'linux' or os.geteuid() == 0:
        raise DiagnosticError('Use selected non-root CPython3.13 on Linux')
    result = controls()
    result['raw_preservation_sensitivity'] = preservation_controls(output)
    permission_file = output / 'permission-control'
    permission_file.write_bytes(b'owned')
    permission_file.chmod(0)
    try:
        try:
            permission_file.read_bytes()
        except OSError as error:
            exact(error.errno, errno.EACCES)
            result['actual_nonroot_permission'] = {'errno': error.errno}
        else:
            raise DiagnosticError('Filesystem does not enforce the required EACCES fixture')
    finally:
        permission_file.chmod(0o600)
    # Existing real state+Session transport succeeds independently of the new entry.
    from session_support import Installation
    with Installation() as control:
        assert control.root.is_relative_to(output)
        control.prepare()
        control.error('session.status', 'session_not_found')
        result['existing_state_session_control'] = str(control.root)
    result.update(python=sys.executable, version=sys.version, uid=os.geteuid(), tmpdir=tempfile.gettempdir())
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', choices=('delivery', 'behavior'), required=True)
    parser.add_argument('--output-root', required=True)
    arguments = parser.parse_args()
    output = Path(arguments.output_root).absolute()
    output.mkdir(parents=True, exist_ok=False)
    temporary = output / 'temporary'
    temporary.mkdir()
    os.environ['TMPDIR'] = str(temporary)
    tempfile.tempdir = str(temporary)
    report = {'suite': arguments.suite, 'argv': sys.argv, 'cwd': os.getcwd(),
              'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).parent,
                                                       text=True).strip(),
              'qualification': None, 'cases': [], 'errors': [], 'failures': [], 'calls': CALLS}
    selected = [item for item in CASES if arguments.suite == 'behavior'
                or item[0] in ('minimal-two-streams', 'binary-middle')]
    report['prescribed_case_ids'] = [name for name, _, _ in selected]
    try:
        report['qualification'] = qualify(output)
        app = OutputFixture(output / 'consumer')
        report['qualification']['consumer'] = app.qualify()
        for name, group, check in selected:
            row = {'id': name, 'group': group, 'first_call': len(CALLS)}
            report['cases'].append(row)
            try:
                app.case(name)
                evidence = check(app)
                if evidence is not None:
                    row['evidence'] = evidence
                row['status'] = 'passed'
            except MissingOutput as error:
                row.update(status='qualified_missing_operation', reason=str(error))
                report['failures'].append({'id': name, 'kind': 'qualified_missing_operation', 'reason': str(error)})
            except AssertionError as error:
                row.update(status='contract_mismatch', reason=str(error))
                report['failures'].append({'id': name, 'kind': 'contract_mismatch', 'reason': str(error),
                                           'traceback': traceback.format_exc()})
            except Exception as error:
                row.update(status='error', reason=type(error).__name__ + ': ' + str(error))
                report['errors'].append({'id': name, 'reason': row['reason'], 'traceback': traceback.format_exc()})
            finally:
                row['last_call'] = len(CALLS)
    except Exception as error:
        report['errors'].append({'id': 'qualification', 'reason': type(error).__name__ + ': ' + str(error),
                                 'traceback': traceback.format_exc()})
    report['unexecuted'] = [name for name, _, _ in selected
                            if name not in {row['id'] for row in report['cases']}]
    if report['unexecuted']:
        report['errors'].append({'id': 'unexecuted', 'cases': report['unexecuted']})
    from generic_checks import LOG
    from session_support import CALLS as SESSION_CALLS, OBSERVATIONS as SESSION_OBSERVATIONS
    from state_sql import SQL_LOG
    report.update(setup_calls=LOG, session_calls=SESSION_CALLS,
                  session_observations=SESSION_OBSERVATIONS, sql=SQL_LOG,
                  producer_observations=(sys.modules['command_support'].OBSERVATIONS
                                         if 'command_support' in sys.modules else []))
    report['status'] = 'error' if report['errors'] else 'failed' if report['failures'] else 'passed'
    (output / 'output-details.json').write_bytes(encoded(report))
    print(json.dumps({'status': report['status'], 'cases': len(report['cases']),
                      'qualified_red': sum(row['status'] == 'qualified_missing_operation' for row in report['cases']),
                      'errors': len(report['errors']), 'failures': len(report['failures'])}))
    return 2 if report['errors'] else 1 if report['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
