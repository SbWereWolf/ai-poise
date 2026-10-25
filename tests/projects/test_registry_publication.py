"""Real registry producer/public CLI consumers; independent exact file oracles."""
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

import pytest

from poise.composition import project_tools
from poise.infrastructure.projects import FileProjectSetup, ProjectSettings
from tests.hook_transport.architecture_support import GeneralScene
from tests.projects.helpers import setup_case

FIXTURES = Path(__file__).parent / 'fixtures/registry_publication'
CASES = json.loads((FIXTURES / 'cases.json').read_text())
WORKTREE = Path(__file__).resolve().parents[2]
SUBJECTS = []


class ScenarioUnavailable(RuntimeError):
    """A fixture/authority/IPC failure cannot satisfy a product RED predicate."""


class RegistryBindingLost(AssertionError):
    pass


class RegistryOwnBindingOverwritten(AssertionError):
    pass


class RegistryBusyOutcomeWrong(AssertionError):
    pass


class RegistryRevisionDiagnosticMissing(AssertionError):
    pass


class RegistryStructuredFailureMissing(AssertionError):
    pass


@dataclass
class Observation:
    code: int
    stdout: str
    stderr: str
    value: dict | None


class Child:
    def __init__(self, scenario, mode, packet, *, hold_publisher=False,
                 hold_probe=False, fault=None):
        self.scenario = scenario
        self.packet = deepcopy(packet)
        self.events = []
        self.buffer = b''
        self.observation = None
        self.completed_capture = None
        self.eof = False
        self.released = False
        self.root = scenario.scratch / ('child-' + str(len(scenario.children)))
        self.root.mkdir()
        event_read, event_write = os.pipe()
        permit_read, self.permit_write = os.pipe()
        self.event_read = event_read
        self.selector = selectors.DefaultSelector()
        self.selector.register(event_read, selectors.EVENT_READ)
        control = {'command': 'project' if mode == 'setup' else 'project-config',
                   'settings': str(scenario.settings),
                   'registry_lock': str(scenario.registry_lock),
                   'event_fd': event_write, 'permit_fd': permit_read,
                   'hold_publisher': hold_publisher, 'hold_probe': hold_probe,
                   'fault': fault, 'io_marker': CASES['io_marker']}
        control_path = self.root / 'control.json'
        control_path.write_text(json.dumps(control))
        self.argv = [sys.executable, '-B', str(FIXTURES / 'public-probe.py'), str(control_path)]
        self.environment = os.environ.copy()
        self.environment['PYTHONPATH'] = os.pathsep.join(
            [str(WORKTREE / 'src'), str(WORKTREE), str(WORKTREE / 'tests')])
        self.environment['PYTHONDONTWRITEBYTECODE'] = '1'
        self.stdout_path = self.root / 'stdout.txt'
        self.stderr_path = self.root / 'stderr.txt'
        with self.stdout_path.open('wb') as stdout, self.stderr_path.open('wb') as stderr:
            self.process = subprocess.Popen(
                self.argv, cwd=WORKTREE, env=self.environment,
                stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                pass_fds=(event_write, permit_read))
        os.close(event_write)
        os.close(permit_read)
        scenario.children.append(self)
        self.process.stdin.write(json.dumps(packet).encode())
        self.process.stdin.close()
        scenario.save()

    def pump(self):
        if self.eof:
            return
        for key, _ in self.selector.select(0.02):
            block = os.read(key.fd, 65536)
            if not block:
                self.eof = True
                self.selector.unregister(key.fd)
                if self.buffer:
                    raise ScenarioUnavailable('Incomplete child event frame')
                return
            self.buffer += block
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                self.events.append(json.loads(line))

    def wait_for(self, *names):
        deadline = time.monotonic() + CASES['watchdog_seconds']
        while True:
            for item in self.events:
                if item['event'] in names:
                    return item
            self.pump()
            if self.eof:
                self.finish()
                raise ScenarioUnavailable('Child ended before reached ' + repr(names))
            if time.monotonic() >= deadline:
                raise ScenarioUnavailable('Fixture IPC watchdog before ' + repr(names))

    def release(self):
        if not self.released:
            try:
                os.write(self.permit_write, b'1')
            except BrokenPipeError:
                pass
            self.released = True

    def finish(self):
        if self.observation is not None:
            return self.observation
        deadline = time.monotonic() + CASES['watchdog_seconds']
        while not (self.process.poll() is not None and self.eof):
            self.pump()
            if time.monotonic() >= deadline:
                self.abort()
                raise ScenarioUnavailable('Fixture child was stopped after its IPC watchdog; actual signal retained')
        code = self.process.wait()
        stdout = self.stdout_path.read_text()
        stderr = self.stderr_path.read_text()
        try:
            value = json.loads(stdout)
        except ValueError:
            value = None
        self.observation = Observation(code, stdout, stderr, value)
        self.completed_capture = self.record()
        self.scenario.save()
        return self.observation

    def abort(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=CASES['cleanup_seconds'])
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.events.append({'event': 'fixture_child_stopped', 'pid': self.process.pid,
                            'terminal_code': self.process.returncode})

    def record(self):
        if self.completed_capture is not None:
            return self.completed_capture
        raw_stdout = self.stdout_path.read_bytes()
        raw_stderr = self.stderr_path.read_bytes()
        try:
            stdout = raw_stdout.decode('utf-8')
        except UnicodeDecodeError:
            stdout = None
        try:
            stderr = raw_stderr.decode('utf-8')
        except UnicodeDecodeError:
            stderr = None
        record = {'pid': self.process.pid, 'argv': self.argv, 'cwd': str(WORKTREE),
                  'explicit_environment': {k: self.environment[k] for k in
                                           ['PYTHONPATH', 'PYTHONDONTWRITEBYTECODE']},
                  'packet': self.packet, 'events': self.events,
                  'terminal_code': self.process.poll(),
                  'stdout': stdout, 'stderr': stderr,
                  'stdout_raw_hex': raw_stdout.hex() if stdout is None else None,
                  'stderr_raw_hex': raw_stderr.hex() if stderr is None else None}
        return record

    def close(self):
        self.release()
        try:
            if self.process.poll() is None:
                self.finish()
        finally:
            self.selector.close()
            os.close(self.event_read)
            os.close(self.permit_write)


class Scenario:
    def __init__(self, project, tmp_path, nodeid):
        self.project = project
        self.scratch = tmp_path / 'publication-scenario'
        self.scratch.mkdir()
        self.nodeid = nodeid
        self.children = []
        self.notes = {}
        self.scenes = {}
        self.settings, _, self.setup_template = setup_case(project)
        fixture_settings = json.loads(self.settings.read_text())
        fixture_settings['lock_seconds'] = CASES['normal_lock_wait']
        fixture_settings['lock_poll_seconds'] = CASES['lock_poll']
        self.settings.write_text(json.dumps(fixture_settings, indent=2) + '\n')
        self.owner = ProjectSettings(self.settings)
        self.root = self.project['root']
        self.registry = self.root / CASES['registry_relative']
        self.registry_lock = self.root / CASES['registry_lock_relative']
        if self.owner.registry != self.registry or self.owner.lock != self.registry_lock:
            raise ScenarioUnavailable('Selected fixture settings do not match independent configured paths')
        self.original_registry = json.loads(self.registry.read_text())
        self.setup(CASES['sentinel'])
        document = json.loads(self.registry.read_text())
        document['projects'][CASES['sentinel']]['metadata'] = deepcopy(CASES['sentinel_metadata'])
        self.registry.write_text(json.dumps(document, indent=2) + '\n')
        self.sentinel_entry = deepcopy(document['projects'][CASES['sentinel']])
        SUBJECTS.append(self)
        self.save()

    def setup_packet(self, name):
        packet = deepcopy(self.setup_template)
        packet.update(request_id='registry-setup-' + name,
                      destination=CASES['destination_parent'] + '/' + name)
        packet['edits'][0]['value'] = name
        return packet

    def setup(self, name):
        return project_tools(self.settings).apply(self.setup_packet(name))

    def scene(self, name):
        if name not in self.scenes:
            created = self.setup(name)
            path = Path(created['config_path'])
            project = {**self.project, 'root': path.parent, 'config_path': path,
                       'cfg': json.loads(path.read_text())}
            parent = self.scratch / name
            parent.mkdir()
            self.scenes[name] = GeneralScene(project, parent, 'REGISTRY_PUBLICATION')
        return self.scenes[name]

    def packet(self, mode, name):
        return (self.setup_packet(name) if mode == 'setup' else
                self.scene(name).transition_packet('registry-profile-' + name))

    def start(self, mode, packet, **controls):
        return Child(self, mode, packet, **controls)

    def run(self, mode, packet, **controls):
        child = self.start(mode, packet, **controls)
        return child, child.finish()

    def snapshot(self):
        files = [self.registry, self.settings]
        configured = self.root / CASES['destination_parent']
        if configured.exists():
            files += [p for p in configured.rglob('*') if p.is_file()
                      and (p.suffix == '.json' or p.name.endswith('.pending'))]
        return {str(p): p.read_text() for p in sorted(set(files))}

    def save(self):
        output = Path(os.environ['POISE_RUN_OUTPUT_DIR'])
        group = os.environ['ARCH_FIX_GROUP']
        output.mkdir(parents=True, exist_ok=True)
        (output / (group + '.subjects.json')).write_text(json.dumps(
            [{'nodeid': s.nodeid, 'settings': str(s.settings), 'registry': str(s.registry),
              'notes': s.notes, 'children': [c.record() for c in s.children]}
             for s in SUBJECTS], ensure_ascii=False, indent=2))

    def close(self):
        for child in self.children:
            child.release()
        failures = []
        try:
            for child in self.children:
                try:
                    child.close()
                except Exception as exc:
                    failures.append(repr(exc))
        finally:
            self.notes['final_files'] = self.snapshot()
            self.save()
        if failures:
            raise ScenarioUnavailable('Fixture child cleanup failures: ' + repr(failures))


@pytest.fixture
def scenario(project, tmp_path, request):
    value = Scenario(project, tmp_path, request.node.nodeid)
    try:
        yield value
    finally:
        value.close()


def require_success(child, result, mode):
    expected = 'created' if mode == 'setup' else 'updated'
    if (result.code != CASES['exit_codes']['success'] or not isinstance(result.value, dict)
            or result.value.get('status') != expected):
        raise ScenarioUnavailable('Real public owner did not succeed: ' + repr(child.record()))
    if not any(e['event'] == 'public_apply_complete' for e in child.events):
        raise ScenarioUnavailable('Missing actual public producer return')
    for event in child.events:
        if event['event'] == 'process_subject':
            if not all(Path(p).is_relative_to(WORKTREE / 'src') for p in event['imports'].values()):
                raise ScenarioUnavailable('Wrong source imports')
            return result.value
    raise ScenarioUnavailable('Missing actual process provenance')


def assert_sentinel(scenario):
    document = json.loads(scenario.registry.read_text())
    assert document['projects'][CASES['sentinel']] == scenario.sentinel_entry


def fault_was_reached(child, result, phase):
    reached = [e for e in child.events if e['event'] == 'fault_reached']
    if len(reached) != 1 or reached[0]['phase'] != phase:
        raise ScenarioUnavailable('Known I/O failpoint was not reached exactly once')
    known = CASES['io_marker'] in result.stderr or (
        isinstance(result.value, dict) and CASES['io_marker'] in result.value.get('reason', ''))
    if not known:
        raise ScenarioUnavailable('Initial failure is not the known storage injection')


@pytest.mark.parametrize('modes', CASES['competing_modes'],
                         ids=['profile-profile', 'setup-setup', 'setup-profile', 'profile-setup'])
def test_competing_public_writers_preserve_bindings(scenario, modes):
    first_mode, second_mode = modes
    first_name, second_name = CASES['first'], CASES['second']
    packets = [scenario.packet(first_mode, first_name), scenario.packet(second_mode, second_name)]
    initial = scenario.snapshot()
    first = scenario.start(first_mode, packets[0], hold_publisher=True)
    first.wait_for('registry_publish_enter')
    second = scenario.start(second_mode, packets[1])
    second.wait_for('public_apply_enter')
    reached = second.wait_for('registry_kernel_blocked', 'registry_publish_enter')
    if reached['event'] == 'registry_kernel_blocked':
        scenario.notes['schedule'] = 'real second flock returned BlockingIOError while first held'
        first.release()
        first_result, second_result = first.finish(), second.finish()
    else:
        scenario.notes['schedule'] = 'actual old unlocked second publication completed first'
        second_result = second.finish()
        require_success(second, second_result, second_mode)
        first.release()
        first_result = first.finish()
    require_success(first, first_result, first_mode)
    require_success(second, second_result, second_mode)
    assert first.process.pid != second.process.pid
    document = json.loads(scenario.registry.read_text())
    scenario.notes.update(before_files=initial, actual_registry=document,
                          after_files=scenario.snapshot())
    expected = {}
    for mode, name in [(first_mode, first_name), (second_mode, second_name)]:
        entry = {'config_path': CASES['destination_parent'] + '/' + name + '/' + CASES['manifest_name']}
        if mode == 'profile':
            entry['harness_config_path'] = str(scenario.scenes[name].path.relative_to(scenario.root))
            assert json.loads(scenario.scenes[name].path.read_text()) == scenario.scenes[name].profile
        expected[name] = entry
    actual = document['projects']
    if (any(actual.get(name) != value for name, value in expected.items())
            or actual.get(CASES['sentinel']) != scenario.sentinel_entry):
        raise RegistryBindingLost('Actual successful publications lost independent input bindings')
    assert all(scenario.snapshot().get(path) == value for path, value in initial.items()
               if path != str(scenario.registry))
    listed = project_tools(scenario.settings).list()
    assert listed['status'] == 'listed'
    assert {x['project'] for x in listed['projects']} == {first_name, second_name, CASES['sentinel']}
    # GREEN also proves the actual kernel primitive and its complete critical interval.
    first_events = [x['event'] for x in first.events]
    second_events = [x['event'] for x in second.events]
    held, intervals = {}, []
    for index, event in enumerate(first.events):
        if event['event'] == 'registry_kernel_acquired':
            held[event['fd']] = index
        elif event['event'] == 'registry_kernel_released':
            intervals.append((held.pop(event['fd']), index))
    assert any(start < first_events.index('registry_publish_enter')
               < first_events.index('registry_publish_complete') < end
               for start, end in intervals)
    assert 'registry_kernel_blocked' in second_events
    assert_sentinel(scenario)


def test_registration_producer_preserves_current_same_binding(scenario):
    name = CASES['first']
    packet = scenario.packet('profile', name)
    child, result = scenario.run('profile', packet)
    require_success(child, result, 'profile')
    document = json.loads(scenario.registry.read_text())
    document['projects'][name]['metadata'] = deepcopy(CASES['own_metadata'])
    scenario.registry.write_text(json.dumps(document, indent=2) + '\n')
    before = scenario.snapshot()
    target = scenario.root / CASES['destination_parent'] / name
    actual = FileProjectSetup(scenario.owner)._register(deepcopy(scenario.original_registry), name, target)
    after = scenario.snapshot()
    scenario.notes.update(producer_reached=True, producer_return=actual,
                          stale_input=scenario.original_registry, before_files=before, after_files=after)
    if actual is not False or before != after:
        raise RegistryOwnBindingOverwritten('Actual registration ignored current same-binding metadata')
    assert_sentinel(scenario)


@pytest.mark.parametrize('mode', CASES['writer_modes'])
def test_busy_public_cli_has_truthful_outcome(scenario, mode):
    packet = scenario.packet(mode, CASES['first'])
    raw = json.loads(scenario.settings.read_text())
    raw['lock_seconds'], raw['lock_poll_seconds'] = CASES['busy_lock_wait'], CASES['lock_poll']
    scenario.settings.write_text(json.dumps(raw, indent=2) + '\n')
    before = scenario.snapshot()
    scenario.registry_lock.parent.mkdir(parents=True, exist_ok=True)
    with scenario.registry_lock.open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        child, initial = scenario.run(mode, packet)
        after_attempt = scenario.snapshot()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    if not isinstance(initial.value, dict):
        raise ScenarioUnavailable('Busy CLI returned unrelated malformed output')
    blocked = initial.value.get('status') == 'rejected' and initial.code == CASES['exit_codes']['rejected']
    reason = initial.value.get('reason', '').lower()
    actionable = ('retry' in reason and 'same request' in reason
                  and (str(scenario.registry_lock).lower() in reason or str(scenario.registry).lower() in reason))
    registry_unchanged = after_attempt[str(scenario.registry)] == before[str(scenario.registry)]
    previous_files_preserved = all(after_attempt.get(path) == value for path, value in before.items())
    own_unchanged = (before == after_attempt if mode == 'profile' else
                     (scenario.root / packet['destination'] / '.project-setup-pending.json').is_file())
    scenario.notes.update(before_files=before, busy_files=after_attempt,
                          kernel_blocked=any(e['event'] == 'registry_kernel_blocked' for e in child.events))
    recovery_child, recovered = scenario.run(mode, packet)
    require_success(recovery_child, recovered, mode)
    complete = scenario.snapshot()
    replay_child, replay = scenario.run(mode, packet)
    replay_value = require_success(replay_child, replay, mode)
    assert replay_value['replayed'] is True
    assert scenario.snapshot() == complete
    assert_sentinel(scenario)
    if not (blocked and actionable and registry_unchanged and previous_files_preserved and own_unchanged
            and scenario.notes['kernel_blocked']):
        raise RegistryBusyOutcomeWrong('Real held configured lock did not produce truthful busy outcome')


def test_stale_profile_cli_reports_actual_revision_and_continuation(scenario):
    packet = scenario.packet('profile', CASES['first'])
    child, first = scenario.run('profile', packet)
    accepted = require_success(child, first, 'profile')
    stale = deepcopy(packet)
    stale['request_id'] += '-stale-new-intent'
    before = scenario.snapshot()
    rejected_child, rejected = scenario.run('profile', stale)
    after = scenario.snapshot()
    if not isinstance(rejected.value, dict):
        raise ScenarioUnavailable('Stale revision CLI did not return real structured output')
    assert rejected.value['status'] == 'rejected'
    assert rejected.code == CASES['exit_codes']['rejected']
    assert before == after
    reason = rejected.value['reason']
    scenario.notes.update(before_files=before, after_files=after,
                          supplied_revision=stale['expected_revision'], actual_revision=accepted['revision'])
    if (stale['expected_revision'] not in reason or accepted['revision'] not in reason
            or 'request' not in reason.lower()):
        raise RegistryRevisionDiagnosticMissing('Real conflict omits actual revision/new-request continuation')
    assert_sentinel(scenario)


@pytest.mark.parametrize('phase', CASES['io_phases'])
def test_profile_io_failure_is_structured_and_recoverable(scenario, phase):
    packet = scenario.packet('profile', CASES['first'])
    scene = scenario.scenes[CASES['first']]
    before = scenario.snapshot()
    child, failed = scenario.run('profile', packet, fault=phase)
    fault_was_reached(child, failed, phase)
    partial = scenario.snapshot()
    assert all(partial.get(path) == value for path, value in before.items()
               if path != str(scenario.registry))
    assert json.loads(scene.path.read_text()) == packet['profile']
    assert len(list(scene.path.parent.glob(scene.path.name + '.*.publication.json.pending'))) == 1
    assert not list(scene.path.parent.glob(scene.path.name + '.*.publication.json'))
    entry = json.loads(scenario.registry.read_text())['projects'][CASES['first']]
    if phase == 'registry':
        assert partial[str(scenario.registry)] == before[str(scenario.registry)]
        assert 'harness_config_path' not in entry
    else:
        assert entry['harness_config_path'] == str(scene.path.relative_to(scenario.root))
    assert_sentinel(scenario)
    recovery_child, recovered = scenario.run('profile', packet)
    accepted = require_success(recovery_child, recovered, 'profile')
    complete = scenario.snapshot()
    replay_child, replay = scenario.run('profile', packet)
    value = require_success(replay_child, replay, 'profile')
    assert value == {**accepted, 'replayed': True}
    assert scenario.snapshot() == complete
    assert not list(scene.path.parent.glob(scene.path.name + '.*.publication.json.pending'))
    scenario.notes.update(before_files=before, partial_files=partial, recovered_files=complete)
    reason = failed.value.get('reason', '').lower() if isinstance(failed.value, dict) else ''
    if (failed.code != CASES['exit_codes']['rejected'] or not isinstance(failed.value, dict)
            or failed.value.get('status') != 'rejected'
            or 'retry' not in reason or 'same request' not in reason):
        raise RegistryStructuredFailureMissing('Known actual OSError lacks structured truthful CLI continuation')


@pytest.mark.parametrize('mode', CASES['writer_modes'])
def test_sequential_completed_replay_preserves_current_registry(scenario, mode):
    packet = scenario.packet(mode, CASES['first'])
    child, first = scenario.run(mode, packet)
    accepted = require_success(child, first, mode)
    other_packet = scenario.packet('profile', CASES['second'])
    other, other_result = scenario.run('profile', other_packet)
    require_success(other, other_result, 'profile')
    before = scenario.snapshot()
    replay_child, replay = scenario.run(mode, packet)
    value = require_success(replay_child, replay, mode)
    assert value['replayed'] is True
    assert value['revision'] == accepted['revision']
    assert value['config_path'] == accepted['config_path']
    assert scenario.snapshot() == before
    assert_sentinel(scenario)
    scenario.notes.update(before_replay=before, after_replay=scenario.snapshot())


@pytest.mark.parametrize('mode', CASES['writer_modes'])
def test_identity_conflict_preserves_current_bindings(scenario, mode):
    packet = scenario.packet('profile', CASES['first'])
    child, result = scenario.run('profile', packet)
    require_success(child, result, 'profile')
    if mode == 'setup':
        conflict = scenario.setup_packet(CASES['first'])
        conflict['destination'] += '-different-manifest'
    else:
        other = scenario.scene(CASES['second'])
        conflict = deepcopy(packet)
        conflict['request_id'] += '-wrong-binding'
        conflict['task_config_path'] = str(other.project['config_path'])
    before = scenario.snapshot()
    rejected_child, rejected = scenario.run(mode, conflict)
    assert isinstance(rejected.value, dict)
    assert rejected.code == CASES['exit_codes']['rejected']
    assert rejected.value['status'] == 'rejected'
    assert rejected.value['reason']
    assert ('identity' if mode == 'setup' else 'project_binding') in rejected.value['reason'].lower()
    assert scenario.snapshot() == before
    assert any(e['event'] == 'public_apply_enter' for e in rejected_child.events)
    assert_sentinel(scenario)
    scenario.notes.update(before_files=before, after_files=scenario.snapshot())


def test_pending_foreign_drift_remains_protective(scenario):
    packet = scenario.packet('profile', CASES['first'])
    child, failed = scenario.run('profile', packet, fault='registry')
    fault_was_reached(child, failed, 'registry')
    other_packet = scenario.packet('profile', CASES['second'])
    other, accepted = scenario.run('profile', other_packet)
    require_success(other, accepted, 'profile')
    before = scenario.snapshot()
    retry_child, rejected = scenario.run('profile', packet)
    assert rejected.code == CASES['exit_codes']['rejected']
    assert isinstance(rejected.value, dict) and rejected.value['status'] == 'rejected'
    assert 'foreign drift' in rejected.value['reason'].lower()
    assert scenario.snapshot() == before
    assert_sentinel(scenario)
    scenario.notes.update(before_protective_retry=before, after_protective_retry=scenario.snapshot())


def test_setup_preparation_leaves_other_publication_available(scenario):
    first_packet = scenario.packet('setup', CASES['first'])
    other_packet = scenario.packet('profile', CASES['second'])
    first = scenario.start('setup', first_packet, hold_probe=True)
    first.wait_for('setup_probe_held')
    other, accepted = scenario.run('profile', other_packet)
    require_success(other, accepted, 'profile')
    scenario.notes['other_completed_before_probe_release'] = True
    first.release()
    require_success(first, first.finish(), 'setup')
    assert_sentinel(scenario)
