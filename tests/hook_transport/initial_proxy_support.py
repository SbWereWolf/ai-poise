"""Sandbox arrangement and process observations, without a proxy implementation."""
import base64
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

from poise.infrastructure.hook_transport import HookService
from poise.interfaces.hook_transport import execute
from tests.hook_transport.helpers import definition, settings, host_environment


FIXTURES = Path(__file__).parent / 'fixtures' / 'initial_proxy'
SOURCE = Path(__file__).resolve().parents[2] / 'src'


def literal(name):
    return json.loads((FIXTURES / name).read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return path


def invoke(command, raw, cwd, session='proxy-fixture-session'):
    env = host_environment(session)
    env.update(INITIAL_PROXY_MARKER='literal inherited marker',
               PYTHONPATH=os.pathsep.join((str(SOURCE), str(SOURCE.parent))))
    return subprocess.run(['/bin/sh', '-c', command, 'native-host'], input=raw,
                          cwd=cwd, env=env, capture_output=True, timeout=20)


class Scene:
    """Three independently registered profiles sharing one native file and lock."""

    def __init__(self, project, parent):
        self.owner_root = Path(project['root'])
        self.root = Path(project['root']) / 'proxy-sandbox'
        self.root.mkdir()
        self.trace = self.root / 'probe-trace'
        self.hooks = self.root / 'native-hooks.json'
        self.lock = self.root / 'native-hooks.lock'
        self.repositories = {}
        for name in ('poise', 'erp', 'other'):
            path = self.root / name
            subprocess.run(['git', 'init', '-q', str(path)], check=True,
                           capture_output=True)
            self.repositories[name] = path
        write(self.hooks, literal('responses.json')['foreign'])
        base = json.loads(Path(settings(project, parent)).read_text())
        self.services, self.definitions, self.receipts, self.probes = [], [], [], []
        paths = []
        ids = ('erp-output-main', 'poise-main', 'poise-erp-selection')
        for number, scope in enumerate(('erp', 'poise', 'erp')):
            probe = self.root / ('child-' + str(number) + '.py')
            probe.write_text('#!' + sys.executable + '\n' +
                             (FIXTURES / 'child_probe.py').read_text())
            probe.chmod(0o700)
            options = {'id': number, 'trace': str(self.trace), 'mode': 'fixed',
                       'python': sys.executable, 'stdout': 'e30K', 'stderr': '',
                       'exit_code': 0}
            write(probe.with_suffix('.json'), options)
            self.probes.append(probe)
            raw = deepcopy(base)
            raw.update(root=str(project['root']), python=str(probe), hooks_file=str(self.hooks),
                       hooks_lock=str(self.lock),
                       repository_scope={'repository': str(self.repositories[scope]),
                                         'timeout_seconds': 3},
                       state='proxy-sandbox/state-' + str(number),
                       lock='proxy-sandbox/state-' + str(number) + '.lock',
                       definitions='proxy-sandbox/definitions-' + str(number),
                       cwd_roots=[str(self.root)])
            path = write(self.root / ('settings-' + str(number) + '.json'), raw)
            paths.append(path)
        # Publish all selected settings through the fixture's existing project
        # owner before admitting HookService. Never bypass the runtime fence.
        general = project['_prepared_harness']
        general.profile['runtime_resources']['hooks'].extend([
            {'installation_id': name, 'settings_path': str(path)}
            for name, path in zip(ids, paths)])
        general.publish(request_id='fixture-initial-proxy-members')
        for number, path in enumerate(paths):
            service = HookService(path)
            value = definition()
            value['id'] = ids[number]
            value['events'] = [value['events'][0]]
            value['events'][0].update(matcher='' if number < 2 else
                                      'startup|resume|compact|clear|fork')
            value['routes'] = {'SessionStart': []}
            receipt = service.install({'request_id': 'legacy-' + str(number),
                'expected_revision': service.revision(), 'definition': value})
            self.services.append(service)
            self.definitions.append(value)
            self.receipts.append(receipt)
        self.before = json.loads(self.hooks.read_text())
        self.members = deepcopy(self.before['hooks']['SessionStart'])

    def outcome(self, number, response=None, *, stdout=None, stderr=b'', code=0,
                mode='fixed'):
        path = self.probes[number].with_suffix('.json')
        value = json.loads(path.read_text())
        if stdout is None:
            stdout = (json.dumps({} if response is None else response,
                                 ensure_ascii=False) + '\n').encode()
        value.update(mode=mode, stdout=base64.b64encode(stdout).decode(),
                     stderr=base64.b64encode(stderr).decode(), exit_code=code)
        write(path, value)

    def raw_event(self, *, source='startup', repository='poise'):
        value = literal('native-event.json')
        value.update(cwd=str(self.repositories[repository]), source=source)
        # Noncanonical whitespace deliberately observes raw-byte forwarding.
        return ('  ' + json.dumps(value, ensure_ascii=True, indent=1) + '\n').encode()

    def calls(self):
        path = self.trace / 'calls.jsonl'
        return [] if not path.exists() else [json.loads(line) for line in path.read_text().splitlines()]

    def legacy(self, raw):
        source = json.loads(raw)['source']
        return [invoke(group['hooks'][0]['command'], raw, self.root)
                for group in self.members
                if not group['matcher'] or re.search(group['matcher'], source)]

    def operation(self, action='publish', *, request_id=None, revision=None):
        owner = self.services[1]
        packet = {'operation': 'session-start-proxy', 'input': {
            'action': action, 'request_id': request_id or ('proxy-' + action),
            'expected_revision': owner.revision() if revision is None else revision}}
        stdout, stderr = io.StringIO(), io.StringIO()
        code = execute('runtime-config', SimpleNamespace(settings=str(owner.settings.path)),
                       io.BytesIO(json.dumps(packet).encode()), stdout, stderr)
        view = json.loads(stdout.getvalue())
        body = json.loads(Path(view['response_path']).read_text())
        return code, body, stderr.getvalue()

    def publish(self):
        result = self.operation()
        groups = json.loads(self.hooks.read_text())['hooks']['SessionStart']
        assert len(groups) == 1, ('INITIAL_PROXY_SINGLE_ENTRY', len(groups), result)
        assert result[0] == 0, result
        assert len(groups[0]['hooks']) == 1
        return groups[0]['hooks'][0]

    def edit_member(self, number, *, timeout=15):
        service = self.services[number]
        value = deepcopy(self.definitions[number])
        value['events'][0].update(status_message='Changed logical member',
                                 timeout_seconds=timeout)
        return service.install({'request_id': 'edit-member-' + str(number),
            'expected_revision': service.revision(), 'definition': value})
