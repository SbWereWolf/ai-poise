"""External reader consumer and independent frozen fixtures; no reader imports.

Existing project/hook fixtures arrange isolated owners. Only a real successful
CLI entry proof permits importing the future Python output/fault boundary.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import threading

from tests.conftest import write_json

FIXTURES = Path(__file__).with_name('fixtures')
SOURCE = Path(__file__).resolve().parents[2] / 'src'
IDENTITY_ENV = {'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING',
                'POISE_SESSION', 'POISE_CONFIG'}


def fixture(name='oracles.json'):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')) + '\n'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class ContentReadUnavailable(AssertionError):
    """Only the actual absent public command counts as pre-code RED."""


def decoded(code, stdout, stderr):
    if (code == 2 and stdout == '' and 'usage:' in stderr
            and "argument command: invalid choice: 'content-read'" in stderr):
        raise ContentReadUnavailable(stderr)
    return code, json.loads(stdout)


def files(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob('*') if p.is_file()}


class Scenario:
    def __init__(self, project, parent, *, identity='reader-fixture-caller'):
        self.base = project
        self.checkout = project['app']
        self.config = project['config_path']
        self.root = project['root']
        self.state = self.root / project['cfg']['paths']['state']
        self.project = project['cfg']['project']
        self.binding = parent / (identity + '.json')
        write_json(self.binding, fixture()['generated_binding'] | {'caller_id': identity})
        self.env = {k: v for k, v in os.environ.items() if k not in IDENTITY_ENV}
        self.env.update(POISE_CALLER_BINDING=str(self.binding),
                        PYTHONPATH=str(SOURCE), PYTHONDONTWRITEBYTECODE='1')
        self.identity = identity
        self.origin = 'generated'
        self.policy = parent / (identity + '-policy.json')
        self.data = self.fill(fixture()['policy_caller'])
        self.save_policy()
        self.write('docs/sample.txt', fixture()['source_texts']['lf'].encode())
        self.proved = False

    @property
    def caller_key(self):
        # Existing public identity contract, independent of its implementation.
        return sha(json.dumps([self.project, self.origin, self.identity],
                              ensure_ascii=False, separators=(',', ':')).encode())

    @property
    def db(self):
        return Path(self.data['registry']['directory']) / self.data['registry']['filename']

    @property
    def lock(self):
        return Path(self.data['registry']['directory']) / self.data['registry']['lock']

    def fill(self, value):
        if isinstance(value, str):
            for token, text in {'@HARNESS_STATE@': str(self.state),
                                '@PROJECT@': self.project,
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
        # Bound in this test's CALL before a reader thread redirects global
        # streams. Control diagnostics never become reader wire bytes.
        print(result.stdout.decode(), end='', file=self.diagnostics_out)
        print(result.stderr.decode(), end='', file=self.diagnostics_err)
        return result.returncode, result.stdout.decode(), result.stderr.decode()

    def invoke(self, request, **kwargs):
        code, out, err = self.raw(request, **kwargs)
        value = decoded(code, out, err)
        assert err == ''
        return value

    def prove(self):
        # This occurs in each test's CALL, never during collection or setup.
        self.diagnostics_out, self.diagnostics_err = sys.stdout, sys.stderr
        result = subprocess.run(self.argv() + ['--help'], capture_output=True,
                                cwd=self.checkout, env=self.env, check=False)
        out, err = result.stdout.decode(), result.stderr.decode()
        if result.returncode != 0:
            decoded(result.returncode, out, err)
        assert result.returncode == 0 and err == '' and 'content-read' in out
        self.proved = True

    def ready(self):
        self.prove()
        assert self.invoke({'operation': 'prepare'}) == (0, self.fill(fixture()['prepare_response']))

    def request(self, *, path='docs/sample.txt', start=2, end=8, complete=False, key='sample'):
        value = deepcopy(fixture()['requests']['read'])
        value['reads'][0].update(key=key, path=path)
        value['reads'][0]['selection'] = (
            {'kind': 'complete', 'start': None, 'end': None} if complete else
            {'kind': 'lines', 'start': start, 'end': end})
        return value

    def read(self, **kwargs):
        code, result = self.invoke(self.request(**kwargs))
        assert code == 0
        return result

    def context(self):
        code, value = self.invoke({'operation': 'context'})
        assert code == 0
        return value

    def reset(self, event='manual-1', operation='reset'):
        code, value = self.invoke({'operation': operation, 'reason': 'manual', 'event_id': event})
        assert code == 0
        return value

    def error(self, actual, name, *, key=None):
        wanted = deepcopy(fixture()['error_envelopes'][name])
        if key is not None:
            wanted['response']['error']['key'] = key
            wanted['response']['error']['field'] = wanted['response']['error']['field'].replace(':sample:', ':' + key + ':')
        assert actual == (wanted['exit'], wanted['response'])

    def row(self, value, *, text, selection, classification, status='content', generation=0):
        expected = self.fill(fixture()['first_response'])
        item = expected['items'][0]
        item.update(hash=sha(text.encode()), range=selection, classification=classification,
                    status=status, context_generation=generation, content_bytes=len(text.encode()),
                    content_lines=len(text.splitlines()), content_follows=status == 'content',
                    previous_read_available=status == 'unchanged')
        if status == 'content':
            item['content'] = text
        else:
            item.pop('content')
        expected['context']['generation'] = generation
        assert value == expected
        return item

    @contextmanager
    def environment(self, monkeypatch):
        with monkeypatch.context() as patch:
            for key in IDENTITY_ENV:
                patch.delenv(key, raising=False)
            for key, value in self.env.items():
                if key in IDENTITY_ENV:
                    patch.setenv(key, value)
            yield

    def transport(self, monkeypatch, request, *, fmt='json', sink=None, clock=None):
        assert self.proved, 'A genuine public CLI proof is required before future import'
        from poise.interfaces.content_reader import execute
        from poise.infrastructure.clock import SystemClock
        out = io.StringIO() if sink is None else sink
        err = io.StringIO()
        with self.environment(monkeypatch):
            if fmt == 'text':
                # The accepted Python output port has no format argument. The
                # genuine CLI owns --format, including in-process fault tests.
                from poise import __main__
                with monkeypatch.context() as patch:
                    patch.setattr(sys, 'argv', ['poise'] + self.argv(fmt)[4:])
                    patch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(canonical(request).encode())))
                    patch.setattr(sys, 'stdout', out)
                    patch.setattr(sys, 'stderr', err)
                    code = __main__.main()
            else:
                code = execute(self.config, self.checkout, self.policy,
                               io.BytesIO(canonical(request).encode()), out, err,
                               SystemClock() if clock is None else clock)
        return code, out.getvalue(), err.getvalue()

    def native_identity(self, identity='reader-native'):
        self.env.pop('POISE_CALLER_BINDING', None)
        self.env['CODEX_THREAD_ID'] = identity
        self.identity, self.origin = identity, 'native'

    def native_hooks(self, parent):
        from tests.hook_transport.helpers import settings, install, event
        from poise.infrastructure.hook_transport import HookService
        self.native_identity()
        self.hooks = HookService(settings(self.base, parent))
        self.installation = install(self.hooks)
        self.event_factory = event
        self.data['admission'] = {'mode': 'native_hook',
                                  'hook_settings': str(self.root / 'hook-settings.json'),
                                  'agent_id': 'primary'}
        self.save_policy()

    def event(self, name='SessionStart'):
        value = self.event_factory(name, self.identity)
        value['cwd'] = str(self.checkout)
        self.hooks.event(self.installation['definition_path'], value)
        binding = self.hooks.latest_binding(self.identity, 'primary')
        # Event ID belongs to the existing HookRegistry, not the reader subject.
        with self.hooks.registry.read_transaction() as db:
            row = db.execute('SELECT id FROM hook_events WHERE binding=? AND event=? ORDER BY id DESC LIMIT 1',
                             (binding['session_id'], name)).fetchone()
        return None if row is None else row[0]


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
    """Independent byte-framed consumer, including concatenated no-final-LF."""
    remaining, frames = raw.encode(), []
    while remaining:
        header, separator, remaining = remaining.partition(b'\n')
        assert separator
        value = json.loads(header)
        item = value['item']
        count = item['content_bytes'] if item['content_follows'] else 0
        assert len(remaining) >= count
        content, remaining = remaining[:count], remaining[count:]
        assert not item['content_follows'] or sha(content) == item['hash']
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
