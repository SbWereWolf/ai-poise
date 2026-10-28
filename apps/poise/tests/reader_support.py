"""Reader-owned external transport, typed literal oracles and state observations."""
from copy import deepcopy
import json
import os
from pathlib import Path

from generic_checks import PY
from session_support import Installation as SessionInstallation, observe_process

FIX = Path(__file__).parent / 'fixtures/reader'
VECTORS = Path(__file__).parent / 'fixtures/reader-contract/vectors.json'
OBSERVATIONS = []


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def exact(actual, expected, path=''):
    assert type(actual) is type(expected), (path, type(actual).__name__, type(expected).__name__)
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), (path, actual, expected)
        for key in expected:
            exact(actual[key], expected[key], path + '/' + key)
    elif isinstance(expected, list):
        assert len(actual) == len(expected), (path, actual, expected)
        for index, (value, target) in enumerate(zip(actual, expected, strict=True)):
            exact(value, target, path + '/' + str(index))
    else:
        assert actual == expected, (path, actual, expected)


def receipt_id(value):
    assert isinstance(value, str) and value.strip() and '\0' not in value, ('invalid receipt ID', value)
    return value


def pointer(value, path):
    for part in path.strip('/').split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class Reader:
    def __enter__(self):
        self.session = SessionInstallation().__enter__()
        self.root = self.session.root / 'documents'
        self.root.mkdir()
        self.ids, self.cursors, self.receipts = {}, [], {}
        self.record = {'root': str(self.root), 'arrangement': [], 'steps': []}
        OBSERVATIONS.append(self.record)
        try:
            self.session.prepare()
            self.session.generated()
            self.session.config.update(deepcopy(load(VECTORS)['configuration']))
            child, record, value = self.session.call('session.ensure')
            if child.returncode != 0 or value['result']['session']['role'] is not None:
                raise RuntimeError('Actual generated Session preparation failed')
            self.identity = value['result']['session']['id']
            self.record['session'] = value
            self.record['initial_rows'] = self.rows()
        except Exception:
            self.session.__exit__(*__import__('sys').exc_info())
            raise
        return self

    def __exit__(self, *args):
        try:
            self.record['before_cleanup_rows'] = self.rows()
            self.record['captures'] = deepcopy(self.ids)
        finally:
            self.session.__exit__(*args)

    def rows(self):
        return {
            'state': self.session.execute(
                'SELECT session_id,data FROM poise_reader_state ORDER BY session_id'),
            'cursors': self.session.execute(
                'SELECT session_id,path,data FROM poise_cursors ORDER BY session_id,path'),
            'sessions': self.session.execute(
                'SELECT id,origin,status,revision FROM poise_sessions ORDER BY id'),
            'bindings': self.session.execute(
                'SELECT identity_key,session_id,inventory FROM poise_bindings ORDER BY identity_key'),
            'roles': self.session.execute(
                'SELECT session_id,role,data FROM poise_role_assignments ORDER BY session_id'),
            'hooks': self.session.execute(
                'SELECT id,session_id,data FROM poise_hook_bindings ORDER BY id'),
            'events': self.session.execute(
                'SELECT seq,binding_id,event_id,event,data FROM poise_hook_events ORDER BY seq')}

    def resolve(self, value, field='', location=()):
        if isinstance(value, str):
            id_role = (not location and field == 'receipt_id'
                       or len(location) == 3 and location[:2] == ('input', 'acknowledged')
                       or len(location) == 4 and location[:2] == ('result', 'items')
                          and location[-1] == 'receipt_id'
                       or location == ('error', 'details', 'receipt_id'))
            if id_role and value.startswith('${') and value.endswith('}'):
                return self.ids[value[2:-1]]
            if field in ('path', 'cwd', 'expected_path') and (value == '$ROOT' or value.startswith('$ROOT/')):
                return str(self.root) + value[5:]
            return value
        if isinstance(value, list):
            return [self.resolve(item, field, location + (index,)) for index, item in enumerate(value)]
        if isinstance(value, dict):
            return {key: self.resolve(item, key, location + (key,)) for key, item in value.items()}
        return value

    def document(self, spec):
        path = Path(self.resolve(spec['path'], 'path'))
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = bytes.fromhex(spec['hex']) if 'hex' in spec else spec['text'].encode('utf-8')
        path.write_bytes(raw)
        version = spec['version']
        os.utime(path, ns=(version['modified_ns'], version['modified_ns']))
        exact(path.stat().st_size, version['size_bytes'])
        exact(path.stat().st_mtime_ns, version['modified_ns'])
        self.record['arrangement'].append({'path': str(path), 'hex': raw.hex(), 'version': version})

    def call(self, request):
        config = self.session.write_config()
        child, record = observe_process(
            [PY, '-I', '-S', '-B', self.session.install / 'bin/poise', '--config', self.session.path],
            json.dumps(request, ensure_ascii=False) + '\n', self.session.state.cwd,
            self.session.environment())
        record['config_raw'] = config.decode('utf-8')
        assert self.session.path.read_bytes() == config, 'Reader changed raw configuration'
        assert child.stderr == b'' and child.stdout.endswith(b'\n'), record
        assert not child.stdout.endswith(b'\n\n'), record
        value = json.loads(child.stdout.decode('utf-8'))
        record['decoded'] = value
        return child.returncode, value

    def step(self, spec):
        if 'replace_document' in spec:
            self.document(load(VECTORS)['documents'][spec['replace_document']])
            exact(str(self.root / 'sample.txt'), self.resolve(spec['expected_path'], 'expected_path'))
            return
        before = self.rows()
        request = self.resolve(deepcopy(spec['request']))
        # Valid next uses the actual previously received and already asserted cursor.
        for item in request.get('input', {}).get('reads', []):
            cursor = item.get('cursor')
            if cursor is not None:
                for accepted, actual in self.cursors:
                    try:
                        exact(cursor, accepted)
                    except AssertionError:
                        continue
                    item['cursor'] = actual
                    break
        code, response = self.call(request)
        for name, location in spec.get('capture', {}).items():
            value = pointer(response, location)
            receipt_id(value)
            if name in self.ids:
                exact(value, self.ids[name])
            else:
                self.ids[name] = value
        expected = self.resolve(spec['expected_response'])
        exact(code, spec['expected_exit'])
        exact(response, expected)
        if response['status'] == 'ok':
            for item, literal in zip(response['result'].get('items', []), expected['result'].get('items', []), strict=True):
                if item['next'] is not None:
                    # The expected cursor has just been compared in the full response above.
                    self.cursors.append((deepcopy(literal['next']), item['next']))
                if item['receipt_id'] is not None and 'text' in item:
                    self.receipts[item['receipt_id']] = deepcopy(literal)
        after = self.rows()
        observation = {'request': request, 'response': response, 'exit': code,
                       'before': before, 'after': after, 'effects': spec.get('effects', {})}
        self.record['steps'].append(observation)
        effects = spec.get('effects', {})
        if code != 0 or effects.get('reader_metadata_unchanged') or effects.get('reader_context_created') is False:
            exact(after, before)
        elif effects.get('receipts_created') == [] and effects.get('acknowledgements_changed') == []:
            exact(after, before)
        for key in ('sessions', 'bindings', 'roles', 'hooks', 'events'):
            exact(after[key], before[key])
        if effects.get('reader_context_created') is True:
            assert any(row[0] == self.identity for row in after['state']), after
        if 'reader_generation' in effects:
            code, context = self.call({'schema': 'stride-poise/v1', 'operation': 'reader.context', 'input': {}})
            exact(code, 0)
            exact(context['result']['generation'], effects['reader_generation'])
        if effects.get('receipts_cleared'):
            # Each formerly received ID must be unknown after reset; failed ACK has no effects.
            for identity, item in self.receipts.items():
                probe = self.receipt_probe(item, response['result']['generation'], [identity])
                untouched = self.rows()
                exit_code, body = self.call(probe)
                exact(exit_code, 2)
                expected_error = deepcopy(load(FIX / 'setup.json')['unknown_receipt'])
                expected_error['error']['details']['receipt_id'] = identity
                exact(body, expected_error)
                exact(self.rows(), untouched)
            self.receipts.clear()
        for token, acknowledged in effects.get('receipt_acknowledged', {}).items():
            identity = self.resolve(token, 'receipt_id')
            original = deepcopy(self.receipts[identity])
            target = deepcopy(original)
            target['status'] = 'unchanged' if acknowledged else 'read'
            if acknowledged:
                del target['text']
            probe = self.receipt_probe(original, response['result']['generation'], [])
            exit_code, body = self.call(probe)
            exact(exit_code, 0)
            data = load(FIX / 'setup.json')
            expected_probe = deepcopy(data['ok_frame'])
            expected_probe['result'] = deepcopy(data['portion_result'])
            expected_probe['result'].update(generation=response['result']['generation'],
                items=[target], text_bytes=0 if acknowledged else len(original['text'].encode('utf-8')))
            exact(body, expected_probe)
        return response

    def receipt_probe(self, item, generation, acknowledged):
        probe = deepcopy(load(FIX / 'setup.json')['receipt_probe_request'])
        probe['input'].update(cwd=str(self.root), generation=generation, acknowledged=acknowledged)
        probe['input']['reads'][0].update(path=item['path'], range=deepcopy(item['range']))
        return probe

    def arrange(self, setup, documents):
        if 'configuration' in setup:
            self.session.config.update(deepcopy(setup['configuration']))
        for name in setup['documents']:
            self.document(documents[name])
        context = setup['reader_context']
        if context is not None:
            operation = 'reader.context' if context['event_id'] is None else 'reader.reset'
            value = {} if operation == 'reader.context' else {
                'reason': context['reason'], 'event_id': context['event_id']}
            expected = deepcopy(load(FIX / 'setup.json')['ok_frame'])
            expected['result'] = context
            self.step({'request': {'schema': 'stride-poise/v1', 'operation': operation, 'input': value},
                       'expected_exit': 0, 'expected_response': expected})
