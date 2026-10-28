"""Independent file-policy client, fixture arrangement and actual effect records."""
from copy import deepcopy
import json
import os
from pathlib import Path

from generic_checks import PY
from reader_support import Reader, exact, pointer, receipt_id
from session_support import Installation, observe_process


FIX = Path(__file__).parent / 'fixtures/fs'
OBSERVER = Path(__file__).with_name('fs_observer.py')
OBSERVATIONS = []


class DiagnosticError(RuntimeError):
    """Preparation/transport/observation failures are never qualified RED."""


class MissingPolicy(AssertionError):
    """Only exact current missing dispatch after valid preparation is RED."""


def load(name):
    return json.loads((FIX / name).read_text(encoding='utf-8'))


class Files:
    def __init__(self, *, reader=False):
        self.reader_mode = reader
        self.ids, self.cursors, self.calls, self.receipts = {}, [], [], {}

    def __enter__(self):
        if self.reader_mode:
            self.reader = Reader().__enter__()
            self.session, self.root = self.reader.session, self.reader.root
            self.ids['session'] = self.reader.identity
        else:
            self.session = Installation().__enter__()
            self.session.generated()
            self.root = self.session.root / 'documents'
            self.root.mkdir()
        self.record = {'root': str(self.root), 'reader': self.reader_mode, 'calls': self.calls,
                       'arrangement': [], 'captures': self.ids}
        OBSERVATIONS.append(self.record)
        self.targets = set()
        try:
            setup = load('setup.json')
            for name in setup['directories']:
                (self.root / name).mkdir(parents=True, exist_ok=True)
            for spec in setup['documents']:
                self.document(spec)
            for spec in setup['aliases']:
                alias = self.root / spec['path']
                alias.symlink_to(self.root / spec['destination'])
                self.targets.add(str(alias))
            for name in setup['watched_absent']:
                self.targets.add(str(self.root / name))
            self.session.config['paths'] = self.bind(setup['policy'], scope='policy')
        except Exception:
            self.__exit__(*__import__('sys').exc_info())
            raise
        return self

    def __exit__(self, *args):
        try:
            if hasattr(self, 'record'):
                self.record['final_files'] = self.facts()
                self.record['final_rows'] = self.rows()
                self.record['database_exists'] = self.session.database.exists()
        finally:
            if self.reader_mode:
                self.reader.__exit__(*args)
            else:
                self.session.__exit__(*args)

    def bind(self, value, *, scope='opaque'):
        """Bind declared protocol slots; every other value is opaque."""
        paths, identities = [], []
        if scope == 'policy':
            paths = [('roots', '*', 'path')]
        elif scope in ('request', 'response') and isinstance(value, dict):
            operation = value.get('operation')
            if scope == 'request' and value.get('schema') == 'stride-poise/v1':
                paths = {
                    'paths.evaluate': [('input', 'base'), ('input', 'effects', '*', 'path')],
                    'files.write': [('input', 'base'), ('input', 'writes', '*', 'path')],
                    'reader.read': [('input', 'cwd'), ('input', 'reads', '*', 'path')],
                    'reader.next': [('input', 'reads', '*', 'cursor', 'path')],
                }.get(operation, []) if isinstance(operation, str) else []
                if operation in ('reader.read', 'reader.next'):
                    identities = [('input', 'acknowledged', '*')]
            elif scope == 'response' and value.get('schema') == 'stride-poise/v1' and 'result' in value:
                result = value['result']
                result_schema = result.get('schema') if isinstance(result, dict) else None
                result_schema = result_schema if isinstance(result_schema, str) else None
                paths = {
                    'poise-fs-result/v1': [('result', 'effects', '*', 'path')],
                    'poise-write-result/v1': [('result', 'policy', 'effects', '*', 'path'),
                                               ('result', 'items', '*', 'path')],
                    'poise-reader/2': [('result', 'items', '*', 'path'),
                                      ('result', 'items', '*', 'next', 'path')],
                }.get(result_schema, [])
                if result_schema == 'poise-reader/2':
                    identities = [('result', 'items', '*', 'receipt_id')]
                elif isinstance(result, dict) and 'session' in result:
                    identities = [('result', 'session', 'id')]
                error = value.get('error')
                if isinstance(error, dict) and error.get('code') in (
                        'file_write_failed', 'reader_source_unavailable',
                        'reader_version_changed', 'reader_file_changed'):
                    paths = [*paths, ('error', 'details', 'path')]

        def matches(location, candidates):
            return any(len(location) == len(pattern) and all(
                type(part) is int if expected == '*' else part == expected
                for part, expected in zip(location, pattern, strict=True)) for pattern in candidates)

        def visit(item, location=()):
            if isinstance(item, dict):
                return {key: visit(child, location + (key,)) for key, child in item.items()}
            if isinstance(item, list):
                return [visit(child, location + (index,)) for index, child in enumerate(item)]
            if isinstance(item, str):
                if matches(location, paths):
                    return self.bound_path(item)
                if matches(location, identities) and item.startswith('${') and item.endswith('}'):
                    return self.ids[item[2:-1]]
            return item

        return visit(value)

    def bound_path(self, value):
        return str(self.root) + value[5:] if value == '$ROOT' or value.startswith('$ROOT/') else value

    def path(self, value):
        return Path(self.bound_path(value))

    def document(self, spec):
        path = self.path(spec['path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = bytes.fromhex(spec['hex']) if 'hex' in spec else spec['text'].encode('utf-8')
        path.write_bytes(raw)
        if 'modified_ns' in spec:
            os.utime(path, ns=(spec['modified_ns'], spec['modified_ns']))
        self.targets.add(str(path))
        if hasattr(self, 'record'):
            self.record['arrangement'].append({'path': str(path), 'hex': raw.hex(),
                                               'modified_ns': path.stat().st_mtime_ns})

    def rows(self):
        return self.reader.rows() if self.reader_mode else None

    def facts(self):
        return {name: Path(name).read_bytes().hex() if Path(name).is_file() else None
                for name in sorted(self.targets)}

    def call(self, packet, *, observed=True, file_limit=False, cwd=None, raw=None):
        config = self.session.write_config()
        request = self.bind(deepcopy(packet), scope='request')
        value = request.get('input')
        reads = value.get('reads') if isinstance(value, dict) else None
        for item in reads if isinstance(reads, list) else []:
            if isinstance(item, dict) and 'cursor' in item:
                for expected, received in self.cursors:
                    try:
                        exact(item['cursor'], expected)
                    except AssertionError:
                        continue
                    item['cursor'] = deepcopy(received)
                    break
        # JSON escapes carry a lone surrogate to the provider's text validation.
        payload = (json.dumps(request, ensure_ascii=True) + '\n').encode() if raw is None else raw
        report = self.session.root / ('fs-observer-' + str(len(self.calls)) + '.json')
        entry = self.session.install / 'bin/poise'
        argv = [PY, '-I', '-S', '-B']
        if observed or file_limit:
            argv += [OBSERVER, '--entry', entry, '--config', self.session.path]
            argv += ['--file-limit-zero'] if file_limit else ['--report', report]
        else:
            argv += [entry, '--config', self.session.path]
        before = {'files': self.facts(), 'rows': self.rows(),
                  'database_exists': self.session.database.exists()}
        child, record = observe_process(argv, payload, cwd or self.session.state.cwd,
                                       self.session.environment())
        self.calls.append(record)
        record['config_hex'] = config.hex()
        record['before'] = before
        record['after'] = {'files': self.facts(), 'rows': self.rows(),
                           'database_exists': self.session.database.exists()}
        if self.session.path.read_bytes() != config or child.stderr:
            raise DiagnosticError('Configuration changed or unexpected actual stderr: ' + repr(record))
        try:
            response = json.loads(child.stdout)
        except (ValueError, UnicodeError) as error:
            raise DiagnosticError('Actual CLI did not return UTF8 JSON') from error
        if not child.stdout.endswith(b'\n') or child.stdout.endswith(b'\n\n'):
            raise DiagnosticError('Actual CLI framing differs')
        record['decoded'] = response
        if observed and not file_limit:
            if not report.is_file():
                raise DiagnosticError('Actual observer report missing')
            events = json.loads(report.read_text())['events']
            record['audit_events'] = events
            names = []
            for event in events:
                path = event.get('path')
                if event['event'] == 'open' and isinstance(path, str):
                    name = Path(path)
                    name = name if name.is_absolute() else Path(cwd or self.session.state.cwd) / name
                    if str(name) in self.targets:
                        names.append(str(name))
            record['target_opens'] = names
            record['database_connections'] = [event for event in events
                                               if event['event'] == 'sqlite3.connect']
        if not self.reader_mode:
            exact(self.session.database.exists(), False)
            if observed and not file_limit:
                exact(record['database_connections'], [])
        return child.returncode, response, record

    def step(self, spec):
        if 'policy' in spec:
            self.session.config['paths'] = self.bind(spec['policy'], scope='policy')
        if spec.get('paths_absent'):
            self.session.config.pop('paths', None)
        for value in spec.get('watch', []):
            self.targets.add(str(self.path(value)))
        code, response, record = self.call(spec['request'], observed=spec.get('observed', True),
                                           file_limit=spec.get('file_limit', False),
                                           cwd=self.path(spec['process_cwd']) if 'process_cwd' in spec else None,
                                           raw=spec.get('raw'))
        for name, location in spec.get('capture', {}).items():
            received = receipt_id(pointer(response, location))
            if name in self.ids:
                exact(received, self.ids[name])
            self.ids[name] = received
        expected = self.bind(spec['expected_response'], scope='response')
        exact(code, spec['expected_exit'])
        exact(response, expected)
        for actual, literal in zip(response.get('result', {}).get('items', [])
                                    if response.get('result') else [],
                                    expected.get('result', {}).get('items', [])
                                    if expected.get('result') else [], strict=True):
            if isinstance(actual.get('next'), dict):
                self.cursors.append((deepcopy(literal['next']), deepcopy(actual['next'])))
            if actual.get('receipt_id') is not None and 'text' in literal:
                self.receipts[literal['receipt_id']] = deepcopy(literal)
        if 'opens' in spec:
            exact(record['target_opens'], [str(self.path(name)) for name in spec['opens']])
        if spec.get('unchanged'):
            exact(record['after'], record['before'])
        if spec.get('rows_unchanged'):
            exact(record['after']['rows'], record['before']['rows'])
        for name, expected_text in spec.get('files', {}).items():
            path = self.path(name)
            if expected_text is None:
                exact(path.exists(), False)
            else:
                exact(path.read_bytes(), expected_text.encode('utf-8'))
        self.effects(spec.get('effects', {}), expected, record)
        return response, record

    def effects(self, effects, expected, record):
        record['declared_effects'] = deepcopy(effects)
        allowed = {'reader_generation', 'receipt_acknowledged',
                   'receipts_created', 'acknowledgements_changed'}
        if effects.keys() - allowed:
            raise DiagnosticError('Unsupported test-owned state expectation')
        if self.reader_mode:
            for key in ('sessions', 'bindings', 'roles', 'hooks', 'events', 'cursors'):
                exact(record['after']['rows'][key], record['before']['rows'][key],
                      'effects/other_rows/' + key)
        if 'receipts_created' in effects or 'acknowledgements_changed' in effects:
            if effects.get('receipts_created') != [] or effects.get('acknowledgements_changed') != []:
                raise DiagnosticError('State fixture must declare the supported empty-effect pair')
            exact(record['after']['rows'], record['before']['rows'],
                  'effects/no_new_receipts_or_acknowledgements')
        if 'reader_generation' in effects:
            code, context, _ = self.call({'schema': 'stride-poise/v1',
                                         'operation': 'reader.context', 'input': {}})
            exact(code, 0, 'effects/context_exit')
            exact(context['result']['generation'], effects['reader_generation'],
                  'effects/reader_generation')
        for token, acknowledged in effects.get('receipt_acknowledged', {}).items():
            identity = self.ids[token[2:-1]] if token.startswith('${') and token.endswith('}') else token
            original = deepcopy(self.receipts[identity])
            target = deepcopy(original)
            target['status'] = 'unchanged' if acknowledged else 'read'
            if acknowledged:
                del target['text']
            generation = expected['result']['generation']
            probe = self.reader.receipt_probe(original, generation, [])
            code, response, _ = self.call(probe)
            frame = deepcopy(load('setup.json')['reader_probe_reply'])
            frame['result'].update(generation=generation, items=[target],
                                   text_bytes=0 if acknowledged else len(original['text'].encode('utf-8')))
            exact(code, 0, 'effects/receipt_acknowledged_exit')
            exact(response, frame, 'effects/receipt_acknowledged')
