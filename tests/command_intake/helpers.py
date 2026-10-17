"""Public SDK/CLI arrangements and independent wire observations for F05.

Only temporary subject data belongs to this module. It does not execute Work
packets, alter Task state, intercept business decisions, or install host hooks.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

from tests.content_reader.helpers import Scenario, canonical, strict_json
from tests.conftest import write_json

FIXTURES = Path(__file__).with_name('fixtures')


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def cases(group):
    return fixture('cases.json')['groups'][group]


class ToolEventsUnavailable(AssertionError):
    """An exact observed rejection of the missing public capability only."""


def install_capability(action):
    from poise.common import PoiseError
    try:
        return action()
    except PoiseError as exc:
        if str(exc) == fixture('gate-errors.json')['absent']:
            raise ToolEventsUnavailable(str(exc)) from exc
        raise


def set_field(value, dotted, replacement):
    keys = dotted.split('.')
    for key in keys[:-1]:
        value = value[key]
    value[keys[-1]] = replacement


class FakeClock:
    fork_safe = False

    def __init__(self, ns=10_000_000_000, domain='fixture-boot-A'):
        self.ns, self.domain = ns, domain

    def observe(self):
        from poise.modules.accounting.clock import ClockObservation
        return ClockObservation(
            audit_utc=datetime(2030, 1, 1, tzinfo=timezone.utc).isoformat(),
            monotonic_ns=self.ns, comparison_domain=self.domain)


class Intake:
    """An isolated installed hook boundary sharing the actual F04 resource."""

    def __init__(self, project, root, monkeypatch, port='sdk'):
        self.s = project if isinstance(project, Scenario) else Scenario(project, root)
        self.seed = self.s.base
        self.root, self.monkeypatch, self.port = root, monkeypatch, port
        self.policy_path = root / 'command-policy.json'
        self.policy = fixture('policy.json')
        self.turn, self.sequence = 'turn1', 0
        self.commands = []
        self.calls = {}
        self.enabled = False
        self.clock = None

    def enable(self):
        # Every current product CALL reaches the real missing capability here.
        # None of the future feature imports or invalid arrangements precedes it.
        for name in ['wait_seconds','recovery_budget_seconds','host_event_budget_seconds']:
            self.s.data['coordination'][name] = 120.0
        self.s.save_policy()
        self.s.ready()
        self.policy_save()
        self.s.data['command_intake'] = {'policy': str(self.policy_path)}
        self.s.save_policy()
        from tests.hook_transport.helpers import definition
        from poise.infrastructure.hook_transport import HookService
        self.definition = definition()
        self.definition['events'].extend(fixture('tool-events.json'))
        path = self.s.hooks.settings.path
        self.settings = json.loads(path.read_text())
        self.settings['tool_intake'] = {'reader_policy': str(self.s.policy)}
        write_json(path, self.settings)
        self.service = HookService(path)
        self.installation = install_capability(lambda: self.service.install({
            'request_id': 'command-events-' + self.s.identity,
            'expected_revision': self.service.revision(),
            'definition': self.definition}))
        self.s.installation = self.installation
        self.s.data['admission']['definition_path'] = self.installation['definition_path']
        self.s.save_policy()
        self.s.hooks = self.service
        for name, text in fixture('cases.json')['common_vectors']['files'].items():
            self.s.write('docs/' + name + '.txt', text.encode('utf-8'))
        self.enabled = True
        return self

    def policy_save(self):
        write_json(self.policy_path, self.policy)

    def configure(self, **fields):
        for path, value in fields.items():
            set_field(self.policy, path, value)
        self.policy_save()

    def mode(self, name):
        self.configure(**{'classifier.mode': name, 'registry.mode': name})

    def command(self, reader, cwd=None):
        return 'cd -- ' + shlex.quote(str(self.s.checkout if cwd is None else cwd)) + ' && ' + reader

    def event_value(self, phase, command, call_id, *, response=None, turn=None):
        value = {'session_id': self.s.identity, 'transcript_path': None,
                 'cwd': str(self.s.checkout), 'hook_event_name': phase,
                 'turn_id': self.turn if turn is None else turn,
                 'tool_name': 'Bash', 'tool_use_id': call_id,
                 'tool_input': {'command': command}}
        if phase == 'PostToolUse':
            value['tool_response'] = response
        return value

    def controls(self):
        # Observe the saved public control schema, not private class fields.
        found = {}
        for file in self.s.root.rglob('*.json'):
            try:
                raw = file.read_bytes()
                value = strict_json(raw)
            except (OSError, ValueError, UnicodeError):
                continue
            if isinstance(value, dict) and value.get('schema') == 'poise-tool-intake-result-1':
                found[str(file)] = (hashlib.sha256(raw).hexdigest(), value)
        return found

    def hook(self, value):
        before = self.controls()
        if self.port == 'cli':
            cp = subprocess.run([
                sys.executable, '-B', '-m', 'poise', 'hook',
                '--settings', str(self.service.settings.path),
                '--definition', self.installation['definition_path']],
                input=canonical(value), text=True, capture_output=True,
                cwd=self.s.checkout, env=self.s.env, check=False)
            print(cp.stdout, end=''); print(cp.stderr, end='', file=sys.stderr)
            assert cp.returncode == 0 and cp.stderr == '', (cp.returncode, cp.stdout, cp.stderr)
            native = strict_json(cp.stdout)
        elif self.clock is not None:
            native = self.service.event(self.installation['definition_path'], value)
        else:
            # The public Python transport composes fresh settings, as the CLI
            # does. A cached HookService is not an oracle for file reloading.
            from poise.interfaces.hook_transport import execute
            out, err = io.StringIO(), io.StringIO()
            args = SimpleNamespace(settings=self.service.settings.path,
                                   definition=Path(self.installation['definition_path']))
            code = execute('hook', args, io.BytesIO(canonical(value).encode()), out, err)
            assert code == 0 and err.getvalue() == '', (code,out.getvalue(),err.getvalue())
            native = strict_json(out.getvalue())
        after = self.controls()
        phase = 'pre' if value['hook_event_name'] == 'PreToolUse' else 'post'
        def matches(x):
            return x['phase'] == phase and (x.get('call') is None or x['call']['tool_use_id'] == value.get('tool_use_id'))
        fresh = [x for key, (digest, x) in after.items()
                 if before.get(key, (None,))[0] != digest and matches(x)]
        if not fresh:
            fresh = [x for _, x in after.values() if matches(x)]
        assert len(fresh) == 1, (value, fresh, native)
        outcome, = fresh
        assert set(outcome) == {'schema','phase','status','context','call','decision','counts','error','response_path'}
        assert set(outcome['decision']) == {'action','reason_code','message','action_hint'}
        assert outcome['phase'] == phase
        if outcome['call'] is not None:
            assert outcome['call']['tool_name'] == value['tool_name']
        assert outcome['decision']['action'] in {'allow','audit','deny','none'}
        assert isinstance(outcome['decision']['reason_code'], str)
        if outcome['decision']['action'] in {'deny','audit'}:
            assert outcome['decision']['message'] and outcome['decision']['action_hint']
        if phase == 'post':
            assert native.get('continue') is not False
            assert native.get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
        return native, outcome

    def pre(self, reader, *, call_id=None, raw=False, **changes):
        self.sequence += 1
        call_id = 'read-' + str(self.sequence) if call_id is None else call_id
        command = reader if raw else self.command(reader)
        event = self.event_value('PreToolUse', command, call_id)
        event.update(changes)
        native, outcome = self.hook(event)
        self.calls[call_id] = event
        return call_id, native, outcome

    def post(self, call_id, response, *, turn=None, **changes):
        value = deepcopy(self.calls[call_id])
        value.update(hook_event_name='PostToolUse', tool_response=response)
        value['turn_id'] = self.turn if turn is None else turn
        value.update(changes)
        return self.hook(value)

    def actual(self, call_id):
        command = self.calls[call_id]['tool_input']['command']
        cp = subprocess.run(['bash', '-c', command], capture_output=True,
                            text=True, cwd=self.s.checkout, check=False)
        self.commands.append({'command': command, 'exit_code': cp.returncode,
                              'stdout': cp.stdout, 'stderr': cp.stderr})
        return {'stdout': cp.stdout, 'stderr': cp.stderr, 'exit_code': cp.returncode}

    def read(self, reader, *, expected='settled'):
        call_id, native, pre = self.pre(reader)
        assert pre['decision']['action'] in {'allow','audit'}, pre
        assert native.get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
        response = self.actual(call_id)
        native, post = self.post(call_id, response)
        assert post['status'] == expected, post
        return pre, post

    def coverage(self):
        # Existing strict F04 envelope helper intentionally has a closed set of
        # seven operations. This new public operation has its own wire checks.
        if self.clock is None:
            code, out, err = self.s.raw({'operation':'coverage'})
        else:
            code, out, err = self.s.transport(self.monkeypatch, {'operation':'coverage'}, clock=self.clock)
        assert code == 0 and err == ''
        value = strict_json(out)
        assert value['schema'] == 'poise-content-read-result-1'
        assert value['operation'] == 'coverage' and value['status'] == 'ready'
        d = value['details']
        assert set(d) == {'schema','documents','pending','counts','omitted'}
        assert d['schema'] == 'poise-read-coverage-1'
        assert set(d['counts']) == {'current_documents','stale_documents','pending','settled'}
        assert set(d['omitted']) == {'documents','ranges','pending'}
        return value

    def use_clock(self, clock):
        from poise.infrastructure.hook_transport import HookService
        self.clock = clock
        self.service = HookService(self.service.settings.path, clock=clock)
        self.s.hooks = self.service

    def document(self, name='A', *, generation=None):
        value = self.coverage()
        if generation is not None:
            assert value['context']['generation'] == generation
        docs = [d for d in value['details']['documents'] if d['path'] == 'docs/' + name + '.txt']
        assert len(docs) == 1, docs
        return docs[0]

    def ranges(self, expected, name='A', *, generation=None, complete=False):
        d = self.document(name, generation=generation)
        assert d['ranges'] == expected and d['complete'] is complete, d
        return d

    def counts(self, *, pending, current=None):
        counts = self.coverage()['details']['counts']
        assert counts['pending'] == pending, counts
        if current is not None:
            assert counts['current_documents'] == current, counts
        return counts

    def advance(self, request_id='generation-1'):
        value = self.s.advance(request_id)
        assert value['context']['generation'] == 1
        repeated = self.s.advance(request_id)
        assert repeated['context']['generation'] == 1
        return value

    def restart_episode(self):
        self.s.event('SessionEnd')
        assert self.s.invoke({'operation':'recover'})[0] == 0
        self.s.event('SessionStart')
        self.s.ready()
        assert self.coverage()['context']['generation'] == 0
        self.counts(pending=0, current=0)
        self.calls.clear()

    def data_snapshot(self):
        with sqlite3.connect(self.s.db.as_uri() + '?mode=ro', uri=True) as db:
            # SQL observes only the existing, agreed F04 public schema. New call
            # text/metadata layout remains an implementation-design decision.
            rows = db.execute('SELECT schema_version FROM registry_schema WHERE singleton=1').fetchall()
            direct = db.execute('SELECT epoch,path_label,normalized_range,status FROM content_read_events ORDER BY id').fetchall()
        return rows, direct

    def plans(self, call_id):
        with sqlite3.connect(self.s.db.as_uri() + '?mode=ro', uri=True) as db:
            rows = db.execute(
                'SELECT epoch,path_label,version_key,plan_json FROM read_events WHERE tool_use_id=? ORDER BY id',
                (call_id,)).fetchall()
        return [(epoch, path, version, strict_json(plan)) for epoch, path, version, plan in rows]


def each_port(project, tmp_path, monkeypatch):
    """Use independent native sessions, never the live agent's caller."""
    from tests.content_reader.helpers import other_project
    for port in ['sdk','cli']:
        root = tmp_path / port
        root.mkdir()
        selected = other_project(project, root)
        intake = Intake(selected, root, monkeypatch, port)
        intake.seed = project
        yield intake.enable()


def named_project(base, parent, name):
    """Prepare a genuinely different project through the existing setup owner."""
    from tests.skills.instruction_delivery_helpers import Scenario as ProjectScenario
    from tests.projects.helpers import bootstrap_fixture_resources
    selected = ProjectScenario(base,parent,name)
    cfg = json.loads(selected.config.read_text())
    if 'runtime_resources' not in cfg:
        cfg = bootstrap_fixture_resources(selected.root/'setup.json',selected.config)
    return Scenario({'app':selected.checkout,'root':selected.config.parent,
                     'config_path':selected.config,'cfg':cfg},parent,
                    identity=name+'-native-reader')
