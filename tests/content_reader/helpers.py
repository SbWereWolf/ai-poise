"""Independent wire consumers and isolated public SDK fixtures for F04.

Every behavioural CALL proves the real public command before arranging native
resources or importing the future port. Missing imports are never expected RED.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

from tests.conftest import write_json

FIXTURES = Path(__file__).with_name('fixtures')
SOURCE = Path(__file__).resolve().parents[2] / 'src'
IDENTITY_ENV = {'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING',
                'POISE_SESSION', 'POISE_CONFIG'}
TOP = {'schema', 'status', 'operation', 'project', 'checkout', 'policy_sha256',
       'context', 'items', 'summary', 'details', 'error', 'response_path'}
ITEM = {'schema_version', 'key', 'status', 'classification', 'path', 'hash',
        'range', 'reader_kind', 'context_generation', 'previous_read_available',
        'content_bytes', 'content_lines', 'content_follows'}
SUMMARY = {'cache_entries', 'event_count', 'content_events', 'unchanged_events',
           'model_content_bytes', 'avoided_bytes'}


def fixture(name='oracles.json'):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False) + '\n'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            assert key not in result, 'Duplicate JSON member'
            result[key] = value
        return result
    def bad(value):
        raise AssertionError('Non-finite JSON number: ' + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)


def typed_equal(actual, expected):
    assert type(actual) is type(expected), (type(actual), type(expected))
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            typed_equal(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected):
            typed_equal(a, e)
    else:
        assert actual == expected


class ContentReadUnavailable(AssertionError):
    """Only the actual absent public command counts as pre-code RED."""


def decoded(code, stdout, stderr):
    if (code == 2 and stdout == '' and 'usage:' in stderr
            and "argument command: invalid choice: 'content-read'" in stderr):
        raise ContentReadUnavailable(stderr)
    return code, strict_json(stdout)


def files(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob('*') if p.is_file()}


class Scenario:
    def __init__(self, project, parent, *, identity='reader-fixture-caller'):
        self.base, self.parent = project, parent
        self.checkout, self.config = project['app'], project['config_path']
        from tests.hook_transport.helpers import harness_profile
        self.config = harness_profile(project, parent, task_adapter=True)
        self.root = project['root']
        self.state = self.root / project['cfg']['paths']['state']
        self.project = project['cfg']['project']
        self.binding = parent / (identity + '.json')
        write_json(self.binding, fixture()['generated_binding'] | {'caller_id': identity})
        self.env = {k: v for k, v in os.environ.items() if k not in IDENTITY_ENV}
        self.env.update(POISE_CALLER_BINDING=str(self.binding), PYTHONPATH=str(SOURCE),
                        PYTHONDONTWRITEBYTECODE='1')
        self.identity, self.origin = identity, 'generated'
        self.policy = parent / (identity + '-policy.json')
        self.data = self.fill(fixture()['policy'])
        self.save_policy()
        self.write('docs/sample.txt', fixture()['source_texts']['lf'].encode())
        self.proved = False
        self.hooks = None
        self.episode = None
        self.resource = None
        self.checkout_keys = {}
        self.registry_path = None
        self.diagnostics_out, self.diagnostics_err = sys.stdout, sys.stderr

    @property
    def caller_key(self):
        return sha(json.dumps([self.project, self.origin, self.identity],
                              ensure_ascii=False, separators=(',', ':')).encode())

    @property
    def db(self):
        assert self.registry_path is not None, 'Prepare a real own resource first'
        return self.registry_path

    def fill(self, value):
        if isinstance(value, str):
            for token, text in {'@ROOT@': str(self.root), '@PROJECT@': self.project,
                                '@CHECKOUT@': str(self.checkout),
                                '@CALLER_KEY@': self.caller_key,
                                '@POLICY_SHA256@': sha(self.policy.read_bytes())
                                if self.policy.exists() else ''}.items():
                value = value.replace(token, text)
            return value
        if isinstance(value, dict):
            return {k: self.fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.fill(v) for v in value]
        return value

    def save_policy(self):
        write_json(self.policy, self.data)

    def write(self, path, raw):
        target = self.checkout / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return target

    def argv(self, fmt='json'):
        return [sys.executable, '-B', '-m', 'poise', 'content-read',
                '--config', str(self.config), '--checkout', str(self.checkout),
                '--policy', str(self.policy), '--format', fmt]

    def raw(self, request, fmt='json', *, env=None, raw=None):
        result = subprocess.run(self.argv(fmt),
                                input=canonical(request).encode() if raw is None else raw,
                                capture_output=True, cwd=self.checkout,
                                env=self.env if env is None else env, check=False)
        print(result.stdout.decode(), end='', file=self.diagnostics_out)
        print(result.stderr.decode(), end='', file=self.diagnostics_err)
        return result.returncode, result.stdout.decode(), result.stderr.decode()

    def invoke(self, request, **kwargs):
        code, out, err = self.raw(request, **kwargs)
        value = decoded(code, out, err)
        assert err == ''
        operation = request.get("operation") if isinstance(request, dict) else None
        self.envelope(value[1], operation=operation, exit_code=code)
        return value

    def envelope(self, value, *, operation=None, exit_code=None, saved_value=None):
        assert type(value) is dict and set(value) == TOP
        typed_equal(value['schema'], 'poise-content-read-result-1')
        operations = {'prepare_template','prepare','read','context','advance_context','recover','adopt'}
        if operation is not None:
            typed_equal(value['operation'], operation if operation in operations else None)
        else:
            assert value['operation'] is None or type(value['operation']) is str and value['operation'] in operations
        statuses = {'ready','rejected','partial','failed','cleaned','already_absent','incomplete','stale','unconfirmed'}
        assert type(value['status']) is str and value['status'] in statuses
        if exit_code == 0:
            assert value['status'] in ({'cleaned','already_absent'} if operation == 'recover' else {'ready'})
        success = value['status'] in {'ready','cleaned','already_absent'}
        assert value['details'] is None or type(value['details']) is dict
        error = value['error']
        errors = {v['code']: v for v in fixture()['errors']}
        def check_error(e):
            assert type(e) is dict and set(e) == {'code','key','field','message','action'}
            assert type(e['code']) is str and e['code'] in errors
            assert e['key'] is None or type(e['key']) is str and e['key']
            assert type(e['field']) is str and e['field']
            typed_equal(e['message'], errors[e['code']]['message'])
            typed_equal(e['action'], errors[e['code']]['action'])
        if error is not None:
            check_error(error)
        if success:
            assert error is None
        unresolved = {'invalid_request','invalid_project','invalid_caller'}
        code = None if error is None else error['code']
        from tests.content_reader.contract_checks import read_root, address_shape
        for key, wanted in [('project',self.project),('checkout',str(self.checkout)),
                            ('policy_sha256',sha(self.policy.read_bytes()))]:
            nullable = code in unresolved or key == 'policy_sha256' and code in {'policy_missing','invalid_policy'}
            if value[key] is None:
                assert nullable
            else:
                typed_equal(value[key],wanted)
        assert type(value['items']) is list
        for item in value['items']:
            assert type(item) is dict and type(item['status']) is str
            if item['status'] == 'failed':
                assert set(item) == {'key','status','error'}
                assert type(item['key']) is str and item['key']
                check_error(item['error']);typed_equal(item['error']['key'],item['key'])
            else:
                assert item['status'] in {'content','unchanged'}
                assert type(item['content_follows']) is bool
                assert set(item) == ITEM | ({'content'} if item['content_follows'] else set())
                for k in ['schema_version','context_generation','content_bytes','content_lines']:
                    assert type(item[k]) is int and item[k] >= 0
                typed_equal(item['schema_version'],1)
                typed_equal(item['reader_kind'],'repository-content-read')
                assert type(item['previous_read_available']) is bool
                for k in ['key','path','hash','range','classification']:
                    assert type(item[k]) is str and item[k]
                assert item['classification'] in {'first_read','new_range','content_changed','new_context_generation','exact_repeat'}
                assert len(item['hash']) == 64 and all(c in '0123456789abcdef' for c in item['hash'])
                assert item['content_follows'] == (item['status']=='content')
                if item['content_follows']:
                    assert type(item['content']) is str
                    assert len(item['content'].encode()) == item['content_bytes']
                    assert len(item['content'].splitlines()) == item['content_lines']
                    assert sha(item['content'].encode()) == item['hash']
        if operation == 'read' and success:
            assert value['items'] and all(v['status'] != 'failed' for v in value['items'])
        if value['status'] == 'partial':
            assert any(v['status']=='failed' for v in value['items'])
            assert any(v['status'] in {'content','unchanged'} for v in value['items'])
        if value['status']=='rejected':
            assert error is not None and value['items']==[]
        if operation != 'read' and operation is not None:
            assert value['items'] == []
        if value['summary'] is not None:
            assert type(value['summary']) is dict and set(value['summary']) == SUMMARY
            assert all(type(n) is int and n >= 0 for n in value['summary'].values())
        if value['status']=='ready' and operation in {'read','context','advance_context','prepare','adopt'}:
            assert value['context'] is not None and value['summary'] is not None
        if operation=='prepare_template':
            assert value['context'] is None and value['summary'] is None
        if value['context'] is not None:
            c = value['context']
            assert type(c) is dict and set(c) == {'caller_key','generation','admission_mode','native_episode','checkout_key'}
            assert type(c['generation']) is int and c['generation'] >= 0
            typed_equal(c['admission_mode'],'native_hook')
            typed_equal(c['caller_key'],self.caller_key)
            assert type(c['checkout_key']) is str and c['checkout_key']
            episode = c['native_episode']
            assert type(episode) is dict and set(episode)=={'project_id','session_id','binding_id','start_event_id'}
            assert all(type(episode[k]) is str and episode[k] for k in ['project_id','session_id','binding_id'])
            assert type(episode['start_event_id']) is int and episode['start_event_id']>0
            if self.episode is not None:
                typed_equal(episode,self.episode)
            if str(self.checkout) in self.checkout_keys:
                typed_equal(c['checkout_key'],self.checkout_keys[str(self.checkout)])
            else:
                assert c['checkout_key'] not in self.checkout_keys.values()
                self.checkout_keys[str(self.checkout)] = c['checkout_key']
        if operation in {'read','context'}:
            assert value['details'] is None
        path = value['response_path']
        if path is None:
            assert not success
        else:
            assert type(path) is str and Path(path).is_absolute()
            control = Path(self.data['storage']['control_response_root'])
            if operation == 'read' and success and self.resource is not None:
                address_shape(path,read_root(self))
            else:
                assert any(Path(path).is_relative_to(root) for root in
                           (Path(self.data['session']['temporary_root']),control))
            if operation == 'prepare_template':
                assert Path(path).is_relative_to(control)
            typed_equal(strict_json(Path(path).read_bytes()), value if saved_value is None else saved_value)
        return value

    def prove(self):
        self.diagnostics_out, self.diagnostics_err = sys.stdout, sys.stderr
        result = subprocess.run(self.argv() + ['--help'], capture_output=True,
                                cwd=self.checkout, env=self.env, check=False)
        out, err = result.stdout.decode(), result.stderr.decode()
        if result.returncode != 0:
            decoded(result.returncode, out, err)
        assert result.returncode == 0 and err == '' and 'content-read' in out
        self.proved = True

    def native_identity(self, identity='reader-native'):
        self.env.pop('POISE_CALLER_BINDING', None)
        self.env['CODEX_THREAD_ID'] = identity
        self.identity, self.origin = identity, 'native'

    def native_hooks(self, parent=None):
        assert self.proved
        from tests.hook_transport.helpers import settings, install, event
        from poise.infrastructure.hook_transport import HookService
        self.native_identity(self.identity if self.origin == 'native' else 'reader-native')
        self.hooks = HookService(settings(self.base, self.parent if parent is None else parent))
        self.installation = install(self.hooks)
        self.event_factory = event
        self.data['admission'] = {'hook_settings': str(self.root / 'hook-settings.json'),
                                  'definition_path': self.installation['definition_path']}
        self.save_policy()

    def event(self, name='SessionStart'):
        value = self.event_factory(name, self.identity)
        value['cwd'] = str(self.checkout)
        receipt = self.hooks.event(self.installation['definition_path'], value)
        # End releases the live binding; historical events retain the episode ID.
        binding_id = (self.episode['binding_id'] if name in ('SessionEnd', 'Stop')
                      else self.hooks.latest_binding(self.identity, 'primary')['session_id'])
        with self.hooks.registry.read_transaction() as db:
            row = db.execute('SELECT id FROM hook_events WHERE binding=? AND event=? ORDER BY id DESC LIMIT 1',
                             (binding_id, name)).fetchone()
        if name == 'SessionStart':
            assert row is not None
            self.episode = {'project_id': self.project, 'session_id': self.identity,
                            'binding_id': binding_id, 'start_event_id': row[0]}
        return None if row is None else row[0]

    def template(self):
        code, result = self.invoke({'operation': 'prepare_template'})
        assert code == 0 and result['status'] == 'ready'
        p = Path(self.data['session']['registry_template'])
        typed_equal(result['details'], {'path': str(p), 'schema_version': 2,
                                        'sha256': sha(p.read_bytes()), 'status': 'prepared'})
        return result

    def active(self):
        self.prove()
        if self.hooks is None:
            self.native_hooks()
            self.event()
        if not Path(self.data['session']['registry_template']).exists():
            self.template()

    def ready(self):
        self.active()
        code, result = self.invoke({'operation': 'prepare'})
        assert code == 0 and result['status'] == 'ready'
        details = result['details']
        assert set(details) == {'path', 'registry_path', 'episode'}
        typed_equal(details['episode'], self.episode)
        self.resource, self.registry_path = Path(details['path']), Path(details['registry_path'])
        assert self.resource.is_relative_to(Path(self.data['session']['temporary_root']))
        assert self.db.is_relative_to(self.resource)
        assert self.db.is_file()
        return result

    def request(self, *, path='docs/sample.txt', start=2, end=8, complete=False, key='sample'):
        value = deepcopy(fixture()['requests']['read'])
        value['reads'][0].update(key=key, path=path)
        value['reads'][0]['selection'] = (
            {'kind': 'complete', 'start': None, 'end': None} if complete else
            {'kind': 'lines', 'start': start, 'end': end})
        return value

    def read(self, **kwargs):
        code, result = self.invoke(self.request(**kwargs))
        assert code == 0 and result['status'] == 'ready'
        return result

    def context(self):
        code, value = self.invoke({'operation': 'context'})
        assert code == 0
        return value

    def advance(self, request_id='manual-1', reason='manual'):
        code, value = self.invoke({'operation': 'advance_context',
                                   'reason': reason, 'request_id': request_id})
        assert code == 0
        typed_equal(value['details'], {'request_id': request_id, 'reason': reason})
        return value

    def error(self, actual, name, *, key=None, field=None):
        code, value = actual
        wanted = next(e for e in fixture()['errors'] if e['code'] == name)
        assert code == wanted['single_failure_exit']
        expected = {'code': name, 'key': key,
                    'field': (field if field is not None else wanted['field']).replace('@KEY@', key or 'sample'),
                    'message': wanted['message'], 'action': wanted['action']}
        errors = [v['error'] for v in value['items'] if v['status'] == 'failed']
        if errors:
            assert len(errors) == 1
            typed_equal(errors[0], expected)
        else:
            typed_equal(value['error'], expected)
        return value

    def row(self, value, *, text, selection, classification, status='content', generation=0, key='sample', summary=None):
        self.envelope(value, operation='read', exit_code=0)
        expected = deepcopy(fixture()['first_response']['items'][0])
        expected.update(key=key, hash=sha(text.encode()), range=selection, classification=classification,
                        status=status, context_generation=generation, content_bytes=len(text.encode()),
                        content_lines=len(text.splitlines()), content_follows=status == 'content',
                        previous_read_available=status == 'unchanged')
        if status == 'content':
            expected['content'] = text
        else:
            expected.pop('content')
        typed_equal(value['items'][0], expected)
        assert set(value['items'][0]) == ITEM | ({'content'} if status == 'content' else set())
        assert value['context']['generation'] == generation
        assert value['details'] is None and value['error'] is None
        if summary is not None:
            from tests.content_reader.contract_checks import complete_expected, assert_complete_read
            wanted = complete_expected(self, items=[expected], summary=summary, generation=generation, path=value['response_path'])
            assert_complete_read(self,value,wanted)
        return expected

    @contextmanager
    def environment(self, monkeypatch):
        with monkeypatch.context() as patch:
            for key in IDENTITY_ENV:
                patch.delenv(key, raising=False)
            for key, value in self.env.items():
                if key in IDENTITY_ENV:
                    patch.setenv(key, value)
            yield

    def transport(self, monkeypatch, request, *, sink=None, clock=None, fmt='json'):
        assert self.proved, 'Prove the genuine CLI before importing a future port'
        from poise.interfaces.content_reader import execute
        from poise.infrastructure.clock import SystemClock
        out, err = io.StringIO() if sink is None else sink, io.StringIO()
        with self.environment(monkeypatch):
            if fmt == 'text':
                from poise import __main__
                with monkeypatch.context() as patch:
                    patch.setattr(sys,'argv',['poise']+self.argv(fmt)[4:])
                    patch.setattr(sys,'stdin',io.TextIOWrapper(io.BytesIO(canonical(request).encode())))
                    patch.setattr(sys,'stdout',out);patch.setattr(sys,'stderr',err)
                    code = __main__.main()
            else:
                code = execute(self.config, self.checkout, self.policy,
                               io.BytesIO(canonical(request).encode()), out, err,
                               SystemClock() if clock is None else clock)
        return code, out.getvalue(), err.getvalue()

    def metadata(self):
        with sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True) as db:
            cache = db.execute('SELECT session_id,epoch,path_label,content_sha256,normalized_range,reader_kind,content_bytes,content_lines FROM content_read_cache ORDER BY session_id,epoch,path_label,content_sha256,normalized_range').fetchall()
            events = db.execute('SELECT session_id,epoch,path_label,content_sha256,normalized_range,reader_kind,status,classification,content_bytes,content_lines,model_content_bytes FROM content_read_events ORDER BY id').fetchall()
        return cache, events
@contextmanager
def before_final_guard(monkeypatch, scenario, lock):
    """Gate an existing flock AFTER actual source opening; no production hook."""
    entered, release = threading.Event(), threading.Event()
    opened = set()
    original_open, original_flock = Path.open, fcntl.flock

    def observe_open(path, *args, **kwargs):
        result = original_open(path, *args, **kwargs)
        if path == scenario.checkout / 'docs/sample.txt':
            opened.add(threading.get_ident())
        return result

    def gate(fd, operation):
        if (threading.current_thread().name == 'reader-interleaving'
                and threading.get_ident() in opened and operation & fcntl.LOCK_EX):
            observed, target = os.fstat(fd), lock.stat()
            if (observed.st_dev, observed.st_ino) == (target.st_dev, target.st_ino):
                entered.set()
                assert release.wait(10), 'The reset/end operation did not release the reader'
        return original_flock(fd, operation)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', observe_open)
        patch.setattr(fcntl, 'flock', gate)
        try:
            yield entered, release
        finally:
            release.set()


def text_frames(raw):
    """Consume exact byte frames; failed items have no content metadata/body."""
    remaining, frames = raw.encode(), []
    while remaining:
        header, separator, remaining = remaining.partition(b'\n')
        assert separator
        value = strict_json(header)
        item = value['item']
        assert type(item) is dict and 'status' in item and type(item['status']) is str
        if item['status'] == 'failed':
            assert set(item) == {'key','status','error'}
            assert type(item['key']) is str and item['key']
            error = item['error']
            assert type(error) is dict and set(error) == {'code','key','field','message','action'}
            typed_equal(error['key'],item['key'])
            for field in ['code','field','message','action']:
                assert type(error[field]) is str and error[field]
            vocabulary = {v['code']:v for v in fixture()['errors']}
            assert error['code'] in vocabulary
            for field in ['message','action']:
                typed_equal(error[field],vocabulary[error['code']][field])
            count, follows = 0, False
        else:
            assert item['status'] in {'content','unchanged'}
            assert set(item) == ITEM
            typed_equal(item['schema_version'],1)
            for field in ['content_bytes','content_lines','context_generation']:
                assert type(item[field]) is int and item[field] >= 0
            for field in ['content_follows','previous_read_available']:
                assert type(item[field]) is bool
            typed_equal(item['content_follows'],item['status']=='content')
            typed_equal(item['previous_read_available'],item['status']=='unchanged')
            for field in ['key','path','hash','range','reader_kind','classification']:
                assert type(item[field]) is str and item[field]
            typed_equal(item['reader_kind'],'repository-content-read')
            assert item['classification'] in {'first_read','new_range','content_changed','new_context_generation','exact_repeat'}
            assert len(item['hash'])==64 and all(c in '0123456789abcdef' for c in item['hash'])
            follows = item['content_follows']
            count = item['content_bytes'] if follows else 0
        assert len(remaining) >= count
        content, remaining = remaining[:count], remaining[count:]
        if follows:
            assert sha(content) == item['hash']
            assert len(content.decode('utf-8').splitlines()) == item['content_lines']
        frames.append((value, content))
    return frames


def other_project(base, parent):
    """Separate real registered checkout/configuration via existing setup owner."""
    from tests.skills.instruction_delivery_helpers import Scenario as ProjectScenario
    from tests.projects.helpers import bootstrap_fixture_resources
    selected = ProjectScenario(base, parent, 'beta')
    config = json.loads(selected.config.read_text())
    if 'runtime_resources' not in config:
        config = bootstrap_fixture_resources(selected.root / 'setup.json', selected.config)
    return Scenario({'app': selected.checkout, 'root': selected.config.parent,
                     'config_path': selected.config, 'cfg': config}, parent,
                    identity='beta-reader')


def storage_snapshot(path):
    """Observe isolated SQLite logical values with explicit columns, read-only."""
    if not path.exists():
        return None
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        names = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        result = []
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [row[1] for row in db.execute('PRAGMA table_info(' + quoted + ')')]
            selection = ','.join('"' + c.replace('"', '""') + '"' for c in columns)
            rows = db.execute('SELECT ' + selection + ' FROM ' + quoted
                              + ' ORDER BY ' + selection).fetchall()
            result.append((name, columns, rows))
        return db.execute('PRAGMA user_version').fetchone()[0], result


@contextmanager
def before_reader_commit(monkeypatch, scenario):
    """Pause actual owned registration transaction before SQLite COMMIT.

    Observe connection.commit, connection.execute(COMMIT), and context-manager
    exit. Hook connections stay unchanged; no new reader API is invented.
    """
    entered, release = threading.Event(), threading.Event()
    original = sqlite3.connect

    class CommitBoundary(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.mutation_seen = False
            self.set_trace_callback(self.observe_statement)

        def observe_statement(self, sql):
            # Observe actual executed DML, including cursor/executemany calls;
            # an earlier read-only transaction cannot satisfy this barrier.
            if sql.lstrip().split(None, 1)[0].upper() in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE'):
                self.mutation_seen = True

        def hold(self):
            if (threading.current_thread().name == 'reader-interleaving'
                    and self.in_transaction and self.mutation_seen
                    and not entered.is_set()):
                entered.set()
                assert release.wait(10), 'Competing writer observation did not release commit'

        def commit(self):
            self.hold()
            return super().commit()

        def execute(self, sql, parameters=()):
            if sql.strip().upper().rstrip(';') in ('COMMIT', 'END', 'END TRANSACTION', 'COMMIT TRANSACTION'):
                self.hold()
            return super().execute(sql, parameters)

        def __exit__(self, kind, value, traceback):
            if kind is None:
                self.hold()
            return super().__exit__(kind, value, traceback)

    def connect(database, *args, **kwargs):
        if str(scenario.db) in str(database) or scenario.db.as_uri() in str(database):
            assert 'factory' not in kwargs, 'Review actual connection adapter before replacing factory'
            kwargs['factory'] = CommitBoundary
        return original(database, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', connect)
        try:
            yield entered, release
        finally:
            release.set()
