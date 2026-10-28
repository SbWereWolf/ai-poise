"""Test-owned arrangements; public CLI and external processes are the subjects.

No helper performs the product admission, update/query ordering or hook dispatch.
Expected source relationships and wire data come from independent fixture files.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from tests.conftest import git, write_json


SOURCE = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name('fixtures')
ACTIVE_SCENES = []


def selected_ast():
    value = os.environ.get('POISE_TEST_AST_INDEX')
    assert value, 'Set POISE_TEST_AST_INDEX to the explicitly selected ast-index executable'
    path = Path(value)
    assert path.is_absolute(), 'POISE_TEST_AST_INDEX must be absolute'
    return path


class SourceIndexUnavailable(AssertionError):
    """Only the genuine old public parser refusal represents intended RED."""


def literal(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def decode(completed):
    if (completed.returncode == 2 and completed.stdout == ''
            and 'usage:' in completed.stderr
            and "argument command: invalid choice: 'source-index'" in completed.stderr):
        raise SourceIndexUnavailable('SOURCE_INDEX_UNAVAILABLE: ' + completed.stderr)
    # Import, setup, unknown permissions and malformed JSON keep their true cause.
    return completed.returncode, json.loads(completed.stdout)


def wait_file(path, process=None, seconds=12):
    deadline = time.monotonic() + seconds
    while not path.exists():
        if process is not None and process.poll() is not None:
            raise AssertionError(f'Process exited before reaching {path}: {process.returncode}')
        if time.monotonic() >= deadline:
            raise AssertionError(f'Fixture did not reach {path}')
        time.sleep(0.01)


class Scene:
    def __init__(self, project, parent, *, real=False, connected=False, general=None):
        self.project = project
        self.parent = parent / 'index fixture'
        self.parent.mkdir(parents=True)
        self.checkout = project['app']
        from tests.hook_transport.architecture_support import GeneralScene
        self.general = general
        if self.general is None:
            self.general = GeneralScene(project, self.parent, 'F12_GENERAL_PROFILE', connected=connected)
            self.general.publish()
        self.config = self.general.path
        self.environment = {k: v for k, v in os.environ.items()
                            if not k.startswith('GIT_') and k not in {
                                'CODEX_THREAD_ID', 'CODEX_SESSION_ID', 'POISE_CALLER_BINDING',
                                'POISE_CONFIG', 'POISE_SESSION'}}
        self.environment.update(PYTHONPATH=str(SOURCE / 'src'), PYTHONDONTWRITEBYTECODE='1')
        self.policy = self.parent / 'policy.json'
        self.control = self.parent / 'peer-control.json'
        self.events = self.parent / 'peer-events.jsonl'
        self.hook_trace = self.parent / 'git-hooks.jsonl'
        self.database = self.checkout / '.runtime/ast-index/index.db'
        self.calls = []
        self.peers = []
        self.python = sys.executable
        self.isolated = False
        ACTIVE_SCENES.append(self)
        (self.checkout / '.gitignore').write_text('.runtime/\n.ast-index.yaml\n')
        (self.checkout / '.ast-index.yaml').write_bytes((FIXTURES / 'index.yaml').read_bytes())
        (self.checkout / 'src/invoice.py').write_bytes((FIXTURES / 'alpha.py').read_bytes())
        git(self.checkout, 'add', '.gitignore', 'src/invoice.py')
        git(self.checkout, 'commit', '-m', 'Arrange independent invoice sources')
        self.policy_data = literal('policy.json')
        self.external = selected_ast() if real else None
        if real:
            self.policy_data['query_commands'].append('refs')
        self.policy_data['command_argv'] = ([str(self.external)] if real else [
            sys.executable, '-B', str(FIXTURES / 'peer.py'), str(self.control)])
        self.controls = literal('peer-control.json')
        self.controls['events'] = str(self.events)
        self.save()

    def save(self):
        write_json(self.policy, self.policy_data)
        write_json(self.control, self.controls)

    def argv(self, checkout=None):
        return [self.python, *(['-I'] if self.isolated else []), '-B', '-m', 'poise', 'source-index',
                '--config', str(self.config), '--checkout', str(checkout or self.checkout),
                '--policy', str(self.policy)]

    def invoke(self, operation, arguments=(), *, checkout=None):
        argv = self.argv(checkout)
        payload = json.dumps({'operation': operation, 'arguments': list(arguments)})
        cp = subprocess.run(argv, input=payload, text=True, capture_output=True,
                            cwd=self.parent, env=self.environment, check=False)
        self.calls.append({'argv': argv, 'stdin': payload, 'cwd': str(self.parent),
                           'exit_code': cp.returncode, 'stdout': cp.stdout, 'stderr': cp.stderr})
        write_json(self.parent / 'cli-calls.json', self.calls)
        return decode(cp)

    def require(self):
        code, result = self.invoke('status')
        assert code in (0, 1), self.calls[-1]
        assert result['schema'] == 'poise-source-index-result-1'
        assert result['checkout'] == str(self.checkout)
        assert result['database'] == str(self.database)
        # All later assertions must observe only their own operation's peer calls.
        self.events.write_text('')

    def ready(self):
        code, result = self.invoke('rebuild')
        assert (code, result['status']) == (0, 'ready'), self.calls[-1]
        assert self.database.is_file()
        self.events.write_text('')

    def records(self):
        if not self.events.exists():
            return []
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def query(self, symbol='net_total', **kwargs):
        return self.invoke('query', ['usages', symbol, '--format', 'json'], **kwargs)

    def output(self, result, operation='query'):
        execution = next(row['execution'] for row in reversed(result['observations'])
                         if row['operation'] == operation)
        path = Path(execution['stdout'])
        assert path.is_relative_to(Path(result['checkout']) / '.runtime/ast-index/runs')
        assert Path(execution['stderr']).is_file()
        return path.read_text(encoding='utf-8')

    def sibling(self):
        path = self.parent / 'second worktree'
        git(self.checkout, 'worktree', 'add', '-b', 'fixture-beta', str(path))
        (path / '.ast-index.yaml').write_bytes((FIXTURES / 'index.yaml').read_bytes())
        (path / 'src/invoice.py').write_bytes((FIXTURES / 'beta.py').read_bytes())
        (path / 'src/delivery.py').write_bytes((FIXTURES / 'delivery-beta.py').read_bytes())
        git(path, 'add', 'src/invoice.py', 'src/delivery.py')
        git(path, 'commit', '-m', 'Arrange independent shipment sources')
        return path

    def start(self, operation, arguments=(), *, checkout=None, label='child'):
        output = self.parent / (label + '.stdout')
        errors = self.parent / (label + '.stderr')
        out = output.open('w'); err = errors.open('w')
        marker = self.parent / (label + '.lock-wait')
        selected = Path(checkout or self.checkout)
        observed = [sys.executable, '-B', str(FIXTURES / 'observed_cli.py'),
                    str(marker), str(selected / self.policy_data['paths']['lock']),
                    *self.argv(checkout)[4:]]
        process = subprocess.Popen(observed, stdin=subprocess.PIPE, stdout=out,
                                   stderr=err, text=True, cwd=self.parent,
                                   env=self.environment, start_new_session=True)
        out.close(); err.close()
        process.stdin.write(json.dumps({'operation': operation, 'arguments': list(arguments)}))
        process.stdin.close()
        self.peers.append(process)
        return process, output, errors

    def finish(self, child):
        process, output, errors = child
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
            raise AssertionError('Fixture client did not terminate')
        return decode(subprocess.CompletedProcess([], process.returncode,
                      output.read_text(), errors.read_text()))

    def hooks(self, python=None, example=None):
        """Operator test arrangement, not a product installer or fake event."""
        directory = self.parent / 'operator hooks'
        directory.mkdir(exist_ok=True)
        for event in ('post-commit', 'post-merge'):
            path = directory / event
            import shlex
            command = [
                python or sys.executable, '-I' if self.isolated else '-B',
                str(FIXTURES / 'git_callback.py'),
                str(self.config), str(self.policy), str(self.hook_trace), event]
            if example is not None:
                command.append(str(example))
            script = '#!/usr/bin/env bash\nexec ' + shlex.join(command) + '\n'
            path.write_text(script); path.chmod(0o755)
        git(self.checkout, 'config', 'core.hooksPath', str(directory))
        return directory


def assert_failure(code, result, expected, needle):
    assert code == (2 if expected == 'rejected' else 1), result
    assert result['status'] == expected
    assert needle.lower() in result['reason'].lower(), result
    assert result['recovery'], result


def phases(scene):
    return [row['operation'] for row in scene.records() if row['phase'] == 'entered']
