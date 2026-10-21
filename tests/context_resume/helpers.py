"""Independent native consumers and isolated, existing F04/F05 arrangements.

The current pre-code public refusal is a CALL failure, never a setup error.
No helper replaces product recovery, creates a second ledger or changes live data.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from tests.conftest import write_json
from tests.content_reader.helpers import Scenario, files, strict_json
from tests.command_intake.helpers import Intake
from tests.hook_transport.helpers import definition, event, settings


FIXTURES = Path(__file__).with_name('fixtures')


def fixture(name='vectors.json'):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


class ContextFeatureUnavailable(AssertionError):
    """Only the precise actual unsupported native-event observation is RED."""


class InstalledPackageIncomplete(AssertionError):
    """A concrete independently observed missing-assets assertion, not accepted RED yet."""


def future_definition():
    value = definition()
    value['events'][0]['matcher'] = 'startup|resume|compact|clear|fork'
    value['events'][0]['context_limit'] = 4096
    value['events'].extend(deepcopy(fixture()['native_events']))
    value['events'].extend(json.loads((FIXTURES.parents[1] / 'command_intake'
                                      / 'fixtures/tool-events.json').read_text()))
    return value


def require_capability():
    from poise.common import PoiseError
    from poise.modules.hook_transport.domain import HookDefinition
    packet = future_definition()
    try:
        HookDefinition.parse(packet)
    except PoiseError as exc:
        if str(exc) != fixture()['native_missing_error']:
            raise
        print(json.dumps({'boundary': 'HookDefinition.parse', 'actual_error': str(exc),
                          'requested_events': ['PreCompact', 'PostCompact']}))
        raise ContextFeatureUnavailable(str(exc)) from exc


def card(value):
    output = value['hookSpecificOutput']
    assert output['hookEventName'] == 'SessionStart'
    text = output['additionalContext']
    prefix = fixture()['card_prefix']
    selected = [line[len(prefix):] for line in text.splitlines() if line.startswith(prefix)]
    assert len(selected) == 1, 'The real native consumer must deliver exactly one current card'
    result = strict_json(selected[0])
    assert set(result) == set(fixture()['card_top_keys'])
    assert result['schema'] == 'poise-work-context-1'
    assert type(result['generation']['current']) is int
    assert type(result['actions']) is list
    assert len(text.encode('utf-8')) <= 4096
    assert fixture()['body_marker'] not in text
    assert fixture()['work_marker'] not in text
    return result


def git_identity(root):
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    raw = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain=v1',
                                   '-z', '--untracked-files=all'])
    return head, hashlib.sha256(raw).hexdigest()


def readonly_rows(path, sql, parameters=()):
    with sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True) as db:
        return [tuple(row) for row in db.execute(sql, parameters)]


class Recovery:
    def __init__(self, project, parent, monkeypatch, *, identity='f06-native', port='python'):
        self.s = project if isinstance(project, Scenario) else Scenario(project, parent, identity=identity)
        self.parent, self.monkeypatch, self.port = parent, monkeypatch, port
        self.i = Intake(self.s, parent, monkeypatch)
        self.cycle = 0
        self.outputs = []
        self.events = []
        self.instructions = parent / (identity + '-instructions.json')
        self.catalog = parent / (identity + '-catalog.json')

    def enable(self, *, work=False, check=False, specialized=False, example=None):
        # Admission is tested in the CALL, before any import of future product code.
        require_capability()
        from poise.infrastructure.hook_transport import HookService
        for name in ('wait_seconds', 'recovery_budget_seconds', 'host_event_budget_seconds'):
            self.s.data['coordination'][name] = 120.0
        self.i.policy['registry']['retention']['max_pending_age_seconds'] = 60.0
        self.i.policy_save()
        self.s.data['command_intake'] = {'policy': str(self.i.policy_path)}
        self.s.save_policy()
        self.s.native_identity(self.s.identity)
        self.s.write('docs/sample.txt', fixture()['source_text'].encode())
        self.s.write('src/billing/invoice.py', b'VALUE = 1\n')
        self.s.write('.agents/skills/core/SKILL.md', fixture()['core_text'].encode())
        self.s.write('.agents/skills/domain/SKILL.md', fixture()['domain_text'].encode())
        write_json(self.instructions, fixture()['instruction_policy'])
        write_json(self.catalog, fixture()['instruction_catalog'])
        path = settings(self.s.base, self.parent)
        self.settings = json.loads(path.read_text())
        self.settings['cwd_roots'].append(str(self.s.checkout))
        self.options = deepcopy(fixture()['general_context'] if example is None else example['general_context'])
        self.options['reader_policy'] = str(self.s.policy)
        self.options['instructions'].update(catalog=str(self.catalog), root=str(self.s.checkout),
                                             policy=str(self.instructions))
        self.options['work_owner']['enabled'] = work
        self.options['check_owner']['enabled'] = check
        self.settings['general_context'] = self.options
        self.settings['tool_intake'] = {'reader_policy': str(self.s.policy)}
        if specialized:
            from poise.composition import project_config_tools
            from poise.common import load_config, digest
            from tests.projects.test_update import update_request
            _, cfg, workflows = load_config(self.s.config)
            selected = cfg['runtime_resources']['project_setup']
            request = update_request(self.s.config, digest({'config': cfg, 'workflows': workflows}),
                                     request_id='f06-special-reader', manifest_edits=[{
                                         'path': ['source_reader'], 'value': {
                                             'policy': str(Path(__file__).resolve().parents[2]
                                                           / 'config/development/source-reader.json'),
                                             'receipt_file': 'specialized-reader.json'}}])
            assert project_config_tools(selected).apply(request)['status'] == 'updated'
        write_json(path, self.settings)
        self.service = HookService(path)
        self.installation = self.service.install({'request_id': 'f06-install-' + self.s.identity,
            'expected_revision': self.service.revision(), 'definition': future_definition()})
        self.s.hooks, self.s.installation, self.s.event_factory = self.service, self.installation, event
        self.s.data['admission'] = {'hook_settings': str(path),
                                   'definition_path': self.installation['definition_path']}
        self.s.save_policy()
        self.s.template()
        self.s.event('SessionStart')
        self.s.ready()
        assert self.s.context()['context']['generation'] == 0
        # Explicit fixture setup of generation one. This is not a native transition.
        assert self.s.advance('f06-test-initial-generation')['context']['generation'] == 1
        self.i.service, self.i.installation, self.i.definition = self.service, self.installation, future_definition()
        self.i.enabled = True
        self.binding_id = self.s.episode['binding_id']
        return self

    @property
    def checkpoint(self):
        key = self.s.checkout_keys[str(self.s.checkout)]
        return self.s.resource / 'context' / key / 'work-context.json'

    def generation(self):
        return self.s.context()['context']['generation']

    def event(self, kind, *, source='compact', identity=None, checkout=None, turn=None):
        value = event(kind, self.s.identity if identity is None else identity)
        value['cwd'] = str(self.s.checkout if checkout is None else checkout)
        if kind == 'SessionStart':
            value['source'] = source
        if kind in ('PreCompact', 'PostCompact'):
            value.update(turn_id=turn or 'f06-cycle-' + str(self.cycle), trigger='manual')
        ended_binding = (self.service.latest_binding(value['session_id'], 'primary')
                         if kind == 'SessionEnd' else None)
        if self.port == 'cli':
            cp = subprocess.run([sys.executable, '-B', '-m', 'poise', 'hook',
                '--settings', str(self.service.settings.path), '--definition', self.installation['definition_path']],
                input=json.dumps(value), capture_output=True, text=True, cwd=self.s.checkout, env=self.s.env)
            print(cp.stdout, end=''); print(cp.stderr, end='', file=sys.stderr)
            assert cp.returncode == 0 and cp.stderr == '', (cp.returncode, cp.stdout, cp.stderr)
            result = strict_json(cp.stdout)
        else:
            result = self.service.event(self.installation['definition_path'], value)
        binding = self.service.latest_binding(value['session_id'], 'primary') if kind != 'SessionEnd' else None
        binding_id = ended_binding['session_id'] if binding is None else binding['session_id']
        rows = readonly_rows(self.service.settings.database,
                             'SELECT id,event FROM hook_events WHERE binding=? AND event=? ORDER BY id DESC LIMIT 1',
                             (binding_id, kind))
        assert len(rows) == 1 and rows[0][1] == kind
        self.events.append({'id': rows[0][0], 'kind': kind, 'input': value})
        self.outputs.append(result)
        return result

    def save(self):
        self.cycle += 1
        self.event('PreCompact')
        assert self.checkpoint.is_file(), 'Real admitted PreCompact must save its own checkpoint'
        stored = strict_json(self.checkpoint.read_bytes())
        assert set(stored) == set(fixture()['checkpoint_top_keys'])
        assert stored['schema'] == 'poise-work-context-checkpoint-1'
        assert stored['scope']['project'] == self.s.project
        assert stored['scope']['checkout'] == str(self.s.checkout)
        assert stored['event']['id'] == self.events[-1]['id']
        assert fixture()['body_marker'].encode() not in self.checkpoint.read_bytes()
        return stored

    def compact(self):
        self.event('PostCompact')
        assert self.generation() == 2

    def restore(self, source='compact'):
        value = card(self.event('SessionStart', source=source))
        assert value['scope']['project'] == self.s.project
        assert value['scope']['checkout'] == str(self.s.checkout)
        assert value['scope']['episode'] == self.s.episode
        return value

    def delivery(self, event_id=None):
        selected = self.events[-1]['id'] if event_id is None else event_id
        matches = []
        for path in self.service.settings.observations.rglob(self.service.settings.raw['response_file']):
            value = strict_json(path.read_bytes())
            if (value.get('schema') == 'poise-hook-lifecycle-delivery-1'
                    and value['packet']['event_receipt']['event_id'] == selected):
                matches.append(path)
        assert len(matches) == 1, 'Retry must address a real committed owner delivery'
        return matches[0]

    def retry(self, path=None):
        return self.service.retry_lifecycle(self.installation['definition_path'],
                                            self.delivery() if path is None else path)

    def checkpoint_bytes(self):
        return self.checkpoint.read_bytes()

    def transitions(self):
        return readonly_rows(self.s.db,
            'SELECT session_id,turn_id,epoch,trigger FROM compactions ORDER BY session_id,epoch,turn_id')

    def own_rows(self):
        return self.s.metadata()

    def instructions_now(self):
        from poise.application.project_instructions import ProjectInstructions
        from poise.infrastructure.project_instructions import FileInstructionSource
        return ProjectInstructions(FileInstructionSource(self.catalog, self.s.checkout)).prepare({
            'operation': 'prepare', 'project_config': str(self.s.config), 'checkout': str(self.s.checkout),
            'policy': str(self.instructions), 'paths': fixture()['paths'], 'previous': None})

    def work_runtime(self):
        return self.service.bound_runtime(self.service.latest_binding(self.s.identity, 'primary')['binding_path'])

    def configure(self, options):
        raw = json.loads(self.service.settings.path.read_text())
        raw['general_context'] = options
        write_json(self.service.settings.path, raw)
        from poise.infrastructure.hook_transport import HookService
        self.service = HookService(self.service.settings.path)
        self.s.hooks = self.service

    def sentinel(self):
        path = self.parent / 'foreign-context' / 'sentinel.txt'
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'FOREIGN-F06-SENTINEL\n')
        return path
