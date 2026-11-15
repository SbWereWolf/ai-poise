"""Independent output consumer, literal expectations and real-file observations."""
import base64
from copy import deepcopy
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

from generic_checks import APP
from reader_support import exact


FIX = Path(__file__).parent / 'fixtures/output'
VECTORS = json.loads((FIX / 'vectors.json').read_text())
CALLS = []


class MissingOutput(AssertionError):
    """Exact absent public operation after independent setup qualification."""


class DiagnosticError(RuntimeError):
    """Setup/transport/instrumentation failures do not establish RED."""


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False,
                       separators=(',', ':')) + '\n').encode('utf-8')


def envelope(result=None, error=None, status='ok'):
    return {'schema': 'stride-poise/v1', 'status': status, 'result': result,
            'error': error, 'evidence': []}


def fault(code, details, action, status='rejected', result=None):
    return envelope(result, {'code': code, 'details': details, 'action': action}, status)


MISSING = fault('invalid_request', {'path': '/operation'}, 'correct:request')
RESPONSE_LIMIT = fault('output_response_limit', {'limit': 'max_response_bytes'}, 'reduce:output')


def minimal(*sources):
    return envelope({'schema': 'poise-output-minimal/v1', 'sources': list(sources)})


def metadata(path, captured, actual, extent='same_size'):
    return {'path': str(path), 'captured_bytes': captured, 'bytes': actual, 'extent': extent}


def ranged(path, captured, size, start, count, data, next_start, eof, extent='same_size'):
    return {**metadata(path, captured, size, extent), 'start': start, 'count': count,
            'data': {'encoding': 'base64', 'value': data}, 'next_start': next_start, 'eof': eof}


def ranges(*items):
    return envelope({'schema': 'poise-output-range/v1', 'ranges': list(items)})


def discrepancy(value, indices):
    return fault('output_extent_differs', {'indices': indices}, 'inspect:output',
                 'unavailable', value['result'])


def source_fault(index, reason='io', number=errno.ENOENT):
    return fault('output_source_unavailable', {'index': index, 'reason': reason, 'errno': number},
                 'select:output', 'unavailable')


def raw_sources(paths):
    """Snapshot only the named test fixtures; never read a FIFO/directory body."""
    result = {}
    for name in dict.fromkeys(map(str, paths)):
        path = Path(name)
        try:
            kind = path.stat().st_mode
        except FileNotFoundError:
            result[name] = {'kind': 'absent'}
            continue
        if not stat.S_ISREG(kind):
            result[name] = {'kind': 'nonregular'}
            continue
        try:
            result[name] = {'kind': 'regular', 'content': path.read_bytes()}
        except PermissionError:
            result[name] = {'kind': 'unreadable'}
    return result


def preserved_sources(before, after):
    exact(after, before)


def source_summary(snapshot):
    return {name: {'kind': item['kind'], **({'bytes': len(item['content'])}
            if item['kind'] == 'regular' else {})} for name, item in snapshot.items()}


def preservation_controls(root):
    path = Path(root) / 'raw-preservation-control'
    path.write_bytes(b'ABCDEF')
    original = raw_sources([path])
    detected = []
    for label, changed in [('changed', b'ABZDEF'), ('truncated', b'AB')]:
        path.write_bytes(changed)
        try:
            preserved_sources(original, raw_sources([path]))
        except AssertionError:
            detected.append(label)
        else:
            raise DiagnosticError('Consumer missed ' + label + ' raw fixture')
    path.write_bytes(b'ABCDEF')
    preserved_sources(original, raw_sources([path]))
    return {'path': str(path), 'detected': detected, 'unchanged_control': 'passed'}


def check_reads(events, path, start=None, count=None):
    selected = [item for item in events if item['path'] == str(path)]
    if start is None:
        exact(selected, [])
        return
    assert selected, 'No observed source reads after successful nonempty range'
    assert all(type(item['requested']) is int and 0 <= item['requested'] <= count
               for item in selected), selected
    assert sum(item['actual'] for item in selected) == count, selected
    assert all(start <= item['offset'] <= start + count
               and item['offset'] + item['actual'] <= start + count for item in selected), selected


def controls():
    for actual, expected in ((True, 1), (1, True), (1.0, 1), ('QUI=', 'Qv8K')):
        try:
            exact(actual, expected)
        except AssertionError:
            pass
        else:
            raise DiagnosticError('Exact consumer accepts a wrong scalar/value')
    expected = discrepancy(minimal(metadata('/source', 6, 2, 'shorter')), [0])
    wrong_size, wrong_status = deepcopy(expected), deepcopy(expected)
    wrong_size['result']['sources'][0]['bytes'] = 6
    wrong_status['status'] = 'ok'
    for value in (wrong_size, wrong_status):
        try:
            exact(value, expected)
        except AssertionError:
            pass
        else:
            raise DiagnosticError('Consumer accepts concealed loss or false success')
    for vector in VECTORS['ranges']:
        raw = b'' if vector['fixture'] == 'empty' else bytes.fromhex(VECTORS[vector['fixture'] + '_hex'])
        selected = raw[vector['start']:vector['start'] + vector['count']]
        exact(len(selected), vector['actual'])
        exact(base64.b64encode(selected).decode('ascii'), vector['base64'])
    bad_reads = [
        [{'path': '/source', 'requested': -1, 'actual': 1, 'offset': 1}],
        [{'path': '/source', 'requested': 1, 'actual': 1, 'offset': 0}],
        [{'path': '/source', 'requested': 2, 'actual': 2, 'offset': 1}]]
    for events in bad_reads:
        try:
            check_reads(events, '/source', 1, 1)
        except AssertionError:
            pass
        else:
            raise DiagnosticError('Consumer accepts unbounded/prefix/oversize read')
    return {'typed_comparator': 'qualified', 'literal_base64_vectors': len(VECTORS['ranges']),
            'loss_and_status_negative_controls': 2,
            'read_observation_negative_controls': len(bad_reads)}


class OutputFixture:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True)
        self.install = self.root / 'standalone app'
        shutil.copytree(APP, self.install,
                        ignore=shutil.ignore_patterns('tests', 'docs', 'fixtures', 'config', '__pycache__'))
        self.environment = {'PATH': str(Path(sys.executable).parent) + ':/usr/bin:/bin',
                            'LANG': 'C.UTF-8', 'TMPDIR': str(self.root)}
        self.qualified = False
        self.case('qualification')

    def case(self, name):
        self.directory = self.root / 'cases' / name
        self.directory.mkdir(parents=True)
        self.config = deepcopy(VECTORS['base_config'])
        self.config_path = self.directory / 'poise.json'
        self.paths = {'binary': self.directory / 'stdout α.bin',
                      'unicode': self.directory / 'unicode.bin',
                      'empty': self.directory / 'stderr.bin'}
        for name, path in self.paths.items():
            path.write_bytes(b'' if name == 'empty' else bytes.fromhex(VECTORS[name + '_hex']))

    def descriptor(self, name='binary', captured=6):
        return {'path': str(self.paths[name]), 'captured_bytes': captured}

    def call(self, operation, value, *, mode='subject', tracked=None):
        self.config_path.write_bytes(encoded(self.config))
        index = len(CALLS)
        audit = self.directory / ('io-' + str(index) + '.json')
        selected = list(map(str, tracked if tracked is not None else self.paths.values()))
        argv = [sys.executable, '-I', '-S', '-B', str(FIX / 'trace_run.py'), mode,
                str(self.install / 'bin/poise'), str(self.config_path), str(audit), json.dumps(selected)]
        packet = {'schema': 'stride-poise/v1', 'operation': operation, 'input': value}
        config_before = self.config_path.read_bytes()
        record = {'argv': argv, 'cwd': str(self.directory), 'env': self.environment,
                  'packet': packet, 'config': deepcopy(self.config)}
        CALLS.append(record)
        before_sources = raw_sources(selected)
        record['source_before'] = source_summary(before_sources)
        try:
            child = subprocess.run(argv, input=encoded(packet), capture_output=True,
                                   cwd=self.directory, env=self.environment, timeout=15)
            record.update(exit_code=child.returncode, stdout_hex=child.stdout.hex(),
                          stderr_hex=child.stderr.hex())
        except subprocess.TimeoutExpired as error:
            record.update(timeout=True, stdout_hex=(error.stdout or b'').hex(),
                          stderr_hex=(error.stderr or b'').hex())
            raise DiagnosticError('Subject/control timeout, not qualified RED') from error
        finally:
            after_sources = raw_sources(selected)
            record['source_after'] = source_summary(after_sources)
            record['raw_sources_preserved'] = False
            preserved_sources(before_sources, after_sources)
            record['raw_sources_preserved'] = True
        if not audit.exists():
            raise DiagnosticError('I/O observer did not finish; full process output retained')
        record['source_reads'] = json.loads(audit.read_text())
        if self.config_path.read_bytes() != config_before:
            raise DiagnosticError('Subject rewrote its working configuration')
        if (self.directory / 'absent').exists():
            raise DiagnosticError('Output operation created its missing Session store parent')
        if child.stderr:
            raise DiagnosticError('Unexpected process stderr')
        if mode == 'control':
            exact(child.returncode, 0)
            return record
        if not child.stdout.endswith(b'\n') or child.stdout.endswith(b'\n\n'):
            raise DiagnosticError('Response is not one complete JSON line')
        try:
            body = json.loads(child.stdout)
        except (ValueError, UnicodeError) as error:
            raise DiagnosticError('Invalid JSON/UTF-8 transport') from error
        exact(set(body), {'schema', 'status', 'result', 'error', 'evidence'})
        exact(body['schema'], 'stride-poise/v1')
        exact(body['evidence'], [])
        exact(child.returncode, {'ok': 0, 'unavailable': 1, 'rejected': 2}[body['status']])
        record['response'] = body
        return record

    def qualify(self):
        control = self.call('unused', {}, mode='control', tracked=[self.paths['binary']])
        check_reads(control['source_reads'], self.paths['binary'], 1, 3)
        record = self.call('not-an-operation', {})
        exact(record['response'], MISSING)
        self.qualified = True
        return {'io_observer': 'real read observed', 'copied_entry': 'qualified existing rejection',
                'root': str(self.root), 'temporary_environment': self.environment}

    def expect(self, operation, value, expected, *, tracked=None):
        assert self.qualified
        record = self.call(operation, value, tracked=tracked)
        record['expected'] = expected
        if record['response'] == MISSING:
            record['qualified_missing_operation'] = True
            raise MissingOutput('Qualified absent ' + operation + ' at /operation')
        exact(record['response'], expected)
        return record
