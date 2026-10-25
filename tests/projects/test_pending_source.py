"""Complete original source, durable producer and actual public retry consumers."""
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from poise.common import digest, load_config
from tests.projects import test_registry_publication as registry_tests
from tests.projects.test_registry_publication import (
    CASES as REGISTRY_CASES, Scenario, ScenarioUnavailable, assert_sentinel,
    fault_was_reached, require_success,
)
from tests.projects.test_update import project_revision, update_request

FIXTURES = Path(__file__).parent / 'fixtures/pending_source'
CASES = json.loads((FIXTURES / 'cases.json').read_text())


class PendingSourceConflictMissing(AssertionError):
    pass


class PendingSourceLegacyAccepted(AssertionError):
    pass


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def require(value, message):
    if not value:
        raise ScenarioUnavailable(message)


def input_fingerprint(value):
    """Build admitted historical fixture INPUT, never the business expected result."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def explanatory(reason, legacy=False):
    text = reason.lower()
    source = any(word in text for word in CASES['source_reason_tokens'])
    action = any(word in text for word in CASES['continuation_tokens'])
    missing = not legacy or any(word in text for word in CASES['unproved_tokens'])
    return source and action and missing


class SourceCase:
    def __init__(self, scenario, nodeid):
        self.scenario = scenario
        self.output = Path(os.environ['POISE_RUN_OUTPUT_DIR']) / 'pending-source-snapshots' / (
            hashlib.sha256(nodeid.encode()).hexdigest()[:16])
        self.workflow_paths = set()
        self.snapshots = {}
        self.packet = None
        self.pending = None
        self.serial = 0
        self.prepare_sources()
        self.scene = scenario.scene(REGISTRY_CASES['first'])
        self.config = self.scene.project['config_path']
        self.read_source()

    def prepare_sources(self):
        """Arrange a complete valid two-workflow blueprint before real public setup."""
        root = self.scenario.root
        blueprint_path = root / 'config/project-blueprint.json'
        blueprint = json.loads(blueprint_path.read_text())
        primary_path = root / blueprint['workflow_sources']['development']['path']
        primary = json.loads(primary_path.read_text())
        primary['future_source'] = deepcopy(CASES['extension_before'])
        writing = deepcopy(primary)
        writing['goal_type'] = 'writing'
        write_json(primary_path, primary)
        write_json(root / CASES['writing_path'], writing)
        blueprint['config']['future_source'] = deepcopy(CASES['extension_before'])
        blueprint['config']['workflows']['writing'] = CASES['writing_path']
        blueprint['workflow_sources']['writing'] = {'path': CASES['writing_path']}
        write_json(blueprint_path, blueprint)

    def read_source(self):
        config = json.loads(self.config.read_text())
        workflows = {}
        for goal, selected in config['workflows'].items():
            path = Path(selected)
            if not path.is_absolute():
                path = self.config.parent / path
            self.workflow_paths.add(path)
            workflows[goal] = json.loads(path.read_text())
        return config, workflows

    def snapshot(self, label):
        self.read_source()
        paths = {p for p in self.scenario.root.rglob('*') if p.is_file()}
        paths.update(p for p in self.workflow_paths if p.is_file())
        values = {str(p): p.read_bytes() for p in sorted(paths)}
        rows = {}
        for index, (name, raw) in enumerate(values.items()):
            destination = self.output / label / 'files' / (str(index) + '-' + Path(name).name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            rows[name] = {'snapshot': str(destination), 'bytes': len(raw),
                          'sha256': hashlib.sha256(raw).hexdigest()}
        manifest = self.output / label / 'manifest.json'
        write_json(manifest, rows)
        self.snapshots[label] = rows
        self.scenario.notes['complete_source_snapshots'] = self.snapshots
        self.scenario.save()
        return values

    def make_packet(self, bound=False):
        packet = self.scenario.packet('profile', REGISTRY_CASES['first'])
        if bound:
            child, result = self.scenario.run('profile', packet)
            accepted = require_success(child, result, 'profile')
            packet = self.scene.transition_packet(
                CASES['bound_request'], revision=accepted['revision'])
            packet['profile']['runtime_resources']['hooks'][0]['installation_id'] = (
                CASES['next_installation'])
        return packet

    def interrupt(self, phase='registry', bound=False):
        self.packet = self.make_packet(bound)
        before_source = self.read_source()
        before = self.snapshot('before-intent')
        self.before_profile = (json.loads(self.scene.path.read_text())
                               if self.scene.path.is_file() else None)
        self.before_registry = json.loads(self.scenario.registry.read_text())
        child, failed = self.scenario.run('profile', self.packet, fault=phase)
        fault_was_reached(child, failed, phase)
        require(failed.code == 2 and isinstance(failed.value, dict)
                and failed.value.get('status') == 'rejected', 'Known partial CLI refusal missing')
        require(any(e['event'] == 'public_apply_enter' for e in child.events),
                'Public interrupted call was not reached')
        require(self.read_source() == before_source, 'Interruption changed original source')
        require(json.loads(self.scene.path.read_text()) == self.packet['profile'],
                'Actual requested partial profile was not written')
        pending = list(self.scene.path.parent.glob(self.scene.path.name + '.*.publication.json.pending'))
        require(len(pending) == 1, 'Expected exactly one own pending intent')
        self.pending = pending[0]
        require(not Path(str(self.pending).removesuffix('.pending')).exists(),
                'Original pending request already has a completed receipt')
        if phase == 'registry':
            require(self.scenario.registry.read_bytes() == before[str(self.scenario.registry)],
                    'Registry failpoint unexpectedly committed registry')
        else:
            require(any(e['event'] == 'registry_publish_complete' for e in child.events),
                    'Receipt failpoint did not follow actual registry commit')
        assert_sentinel(self.scenario)
        self.scenario.notes.update(original_request=deepcopy(self.packet),
                                   original_source=before_source, partial_phase=phase,
                                   pending_path=str(self.pending), bound_profile=bound)
        self.snapshot('partial')

    def update(self, *, manifest_edits=None, workflow_updates=None):
        self.serial += 1
        packet = update_request(
            self.config, project_revision(self.config),
            request_id='pending-source-independent-' + str(self.serial),
            manifest_edits=manifest_edits, workflow_updates=workflow_updates)
        packet['receipt_path'] = 'operations/pending-source-independent-' + str(self.serial) + '.json'
        value = self.scene.owner.apply(packet)
        require(value.get('status') == 'updated' and value.get('changed') is True,
                'Independent real public source update did not complete')
        self.scenario.notes.setdefault('independent_source_updates', []).append(
            {'request': packet, 'result': value})
        self.scenario.save()
        return value

    def change_source(self, kind):
        config, workflows = self.read_source()
        expected_config, expected_workflows = deepcopy(config), deepcopy(workflows)
        before = self.snapshot('before-source-change')
        if kind == 'manifest':
            expected_config['limits']['preview_chars'] += 1
            self.update(manifest_edits=[{'path': ['limits', 'preview_chars'],
                                        'value': expected_config['limits']['preview_chars']}])
        elif kind == 'manifest-extension':
            expected_config['future_source'] = deepcopy(CASES['extension_after'])
            self.update(manifest_edits=[{'path': ['future_source', 'flag'],
                                        'value': CASES['extension_after']['flag']}])
        elif kind in ('workflow-primary', 'workflow-secondary'):
            goal = 'development' if kind == 'workflow-primary' else 'writing'
            instruction = CASES['primary_instruction'] if goal == 'development' else CASES['secondary_instruction']
            require(expected_workflows[goal]['stages'][0]['id'] == 'tests',
                    'Literal fixture stage is not present')
            expected_workflows[goal]['stages'][0]['instruction'] = instruction
            self.update(workflow_updates=[{'goal_type': goal,
                'expected_revision': digest(workflows[goal]), 'changes': [
                    {'op': 'patch_stage', 'id': 'tests', 'set': {'instruction': instruction}}]}])
        elif kind == 'workflow-extension':
            expected_workflows['development']['future_source'] = deepcopy(CASES['extension_after'])
            selected = Path(config['workflows']['development'])
            path = selected if selected.is_absolute() else self.config.parent / selected
            write_json(path, expected_workflows['development'])
        elif kind == 'workflow-set':
            expected_config['workflows'] = {'development': config['workflows']['development']}
            expected_workflows = {'development': workflows['development']}
            write_json(self.config, expected_config)
        else:
            raise ScenarioUnavailable('Unknown declared independent source input')
        # Validate a real input, never derive the expected business outcome from this loader.
        load_config(self.config)
        require(self.read_source() == (expected_config, expected_workflows),
                'Actual full source differs from the independent declared change')
        require(self.pending.is_file(), 'Source arrangement removed the original pending')
        for path in [self.scene.path, self.pending, self.scenario.registry]:
            require(path.read_bytes() == before[str(path)],
                    'Source arrangement changed profile, pending or registry')
        self.scenario.notes.update(source_change_kind=kind,
            source_change_uses_public_update=kind not in ('workflow-extension', 'workflow-set'),
            expected_source=(expected_config, expected_workflows))
        self.scenario.save()

    def retry(self):
        before = self.snapshot('before-retry')
        child, actual = self.scenario.run('profile', self.packet)
        after = self.snapshot('after-retry')
        require(child.packet == self.packet, 'Retry used a changed publication request')
        self.scenario.notes.update(actual_retry=child.record(),
            retry_changed_paths=[p for p in sorted(set(before) | set(after))
                                 if before.get(p) != after.get(p)])
        self.scenario.save()
        return child, actual, before, after

    def assert_pending_read(self, child, expected_document=None):
        opened = [e for e in child.events if e['event'] == 'source_pending_read_open'
                  and e['path'] == str(self.pending)]
        returned = [e for e in child.events if e['event'] == 'source_pending_document_return'
                    and e['path'] == str(self.pending)]
        require(opened and returned, 'Actual product did not read the selected pending record')
        if expected_document is not None:
            require(any(e['document'] == expected_document for e in returned),
                    'Product did not consume the independently injected historical record')
        self.scenario.notes['actual_pending_read_witness'] = returned
        self.scenario.save()

    def assert_conflict(self):
        child, actual, before, after = self.retry()
        self.assert_pending_read(child)
        valid = (actual.code == 2 and isinstance(actual.value, dict)
                 and actual.value.get('status') == 'rejected'
                 and explanatory(actual.value.get('reason', '')) and before == after
                 and self.pending.is_file()
                 and not any(e['event'] == 'source_atomic_write_call' for e in child.events))
        if not valid:
            raise PendingSourceConflictMissing(
                'Original source change did not yield a truthful read-only conflict: '
                + repr({'exit': actual.code, 'result': actual.value,
                        'changed_paths': self.scenario.notes['retry_changed_paths']}))

    def recover(self):
        child, actual, before, after = self.retry()
        accepted = require_success(child, actual, 'profile')
        project = self.packet['project_id']
        expected_registry = deepcopy(json.loads(before[str(self.scenario.registry)]))
        own = expected_registry['projects'][project]
        own['config_path'] = str(Path(self.packet['task_config_path']).relative_to(
            self.scenario.root))
        own['harness_config_path'] = str(Path(self.packet['harness_config_path']).relative_to(
            self.scenario.root))
        assert json.loads(self.scenario.registry.read_text()) == expected_registry
        assert accepted['project'] == project
        assert accepted['config_path'] == self.packet['task_config_path']
        assert accepted['harness_config_path'] == self.packet['harness_config_path']
        require(not self.pending.exists(), 'Successful recovery retains its pending')
        require(json.loads(self.scene.path.read_text()) == self.packet['profile'],
                'Recovery did not publish the requested profile')
        receipt = Path(str(self.pending).removesuffix('.pending'))
        require(json.loads(receipt.read_text())['result'] == accepted,
                'Recovery result does not match its actual committed receipt')
        replay_child, replay = self.scenario.run('profile', self.packet)
        replayed = require_success(replay_child, replay, 'profile')
        assert replayed == {**accepted, 'replayed': True}
        assert self.snapshot('after-replay') == after
        assert_sentinel(self.scenario)


@pytest.fixture
def source_case(project, tmp_path, request, monkeypatch):
    scenario = Scenario(project, tmp_path, request.node.nodeid)
    monkeypatch.setattr(registry_tests, 'FIXTURES', FIXTURES)
    try:
        yield SourceCase(scenario, request.node.nodeid)
    finally:
        scenario.close()


@pytest.mark.parametrize('kind', CASES['source_cases'])
def test_pending_retry_rejects_changed_source(source_case, kind):
    source_case.interrupt()
    source_case.change_source(kind)
    source_case.assert_conflict()


@pytest.mark.parametrize('kind', ['manifest', 'workflow-primary'])
def test_source_drift_after_registry_commit(source_case, kind):
    source_case.interrupt('receipt')
    source_case.change_source(kind)
    source_case.assert_conflict()


@pytest.mark.parametrize('case', ['registry-manifest', 'receipt-workflow-secondary'])
def test_bound_profile_source_drift(source_case, case):
    phase, kind = case.split('-', 1)
    source_case.interrupt(phase, bound=True)
    source_case.change_source(kind)
    source_case.assert_conflict()


@pytest.mark.parametrize('phase', ['registry', 'receipt'])
def test_legacy_pending_without_source_is_not_admitted(source_case, phase):
    source_case.interrupt(phase, bound=True)
    legacy = json.loads((FIXTURES / 'legacy-pending.json').read_text())
    after_registry = deepcopy(source_case.before_registry)
    after_registry['projects'][REGISTRY_CASES['first']]['harness_config_path'] = str(
        source_case.scene.path.relative_to(source_case.scenario.root))
    legacy['request_digest'] = input_fingerprint(source_case.packet)
    legacy['before']['profile'] = input_fingerprint(source_case.before_profile)
    legacy['before']['registry'] = input_fingerprint(source_case.before_registry)
    legacy['after']['profile'] = input_fingerprint(source_case.packet['profile'])
    legacy['after']['registry'] = input_fingerprint(after_registry)
    require(legacy['before']['profile'] != legacy['after']['profile'],
            'Old composite fixture has no lost prior profile identity')
    write_json(source_case.pending, legacy)
    source_case.scenario.notes['independent_legacy_input'] = legacy
    child, actual, before, after = source_case.retry()
    source_case.assert_pending_read(child, legacy)
    valid = (actual.code == 2 and isinstance(actual.value, dict)
             and actual.value.get('status') == 'rejected'
             and explanatory(actual.value.get('reason', ''), legacy=True)
             and before == after and source_case.pending.is_file()
             and not any(e['event'] == 'source_atomic_write_call' for e in child.events))
    if not valid:
        raise PendingSourceLegacyAccepted('Old unprovable composite pending was not preserved/refused: '
                                          + repr({'exit': actual.code, 'result': actual.value,
                                                  'changed_paths': source_case.scenario.notes[
                                                      'retry_changed_paths']}))


def test_source_intent_is_durable_before_profile(source_case):
    packet = source_case.scenario.packet('profile', REGISTRY_CASES['first'])
    child, result = source_case.scenario.run('profile', packet)
    require_success(child, result, 'profile')
    profile_calls = [(i, e) for i, e in enumerate(child.events)
                     if e['event'] == 'source_atomic_write_call'
                     and e['path'] == str(source_case.scene.path)]
    assert len(profile_calls) == 1
    position, effect = profile_calls[0]
    durable = [e for e in child.events[:position]
               if e['event'] == 'source_atomic_write_return'
               and e['path'].endswith('.publication.json.pending')]
    assert len(durable) == 1 and durable[0]['stored_base64'] is not None
    assert effect['pending_visible_at_entry'][durable[0]['path']] == durable[0]['stored_base64']
    payload = base64.b64decode(durable[0]['stored_base64'])
    assert isinstance(json.loads(payload), dict)
    source_case.scenario.notes['actual_durable_before_effect'] = durable
    source_case.snapshot('completed-order-control')


@pytest.mark.parametrize('case', [
    'initial-registry', 'initial-receipt', 'bound-registry', 'bound-receipt',
])
def test_same_source_partial_recovers(source_case, case):
    binding, phase = case.split('-')
    source_case.interrupt(phase, bound=binding == 'bound')
    source_case.recover()


def test_semantic_source_formatting_does_not_conflict(source_case):
    source_case.interrupt()
    before = source_case.read_source()
    for path in [source_case.config, *source_case.workflow_paths]:
        path.write_text(json.dumps(json.loads(path.read_text()), sort_keys=True, separators=(',', ':')))
    assert source_case.read_source() == before
    source_case.recover()


def test_public_source_restoration_recovers_original_intent(source_case):
    source_case.interrupt()
    original = source_case.read_source()
    source_case.change_source('manifest')
    source_case.update(manifest_edits=[{'path': ['limits', 'preview_chars'],
                                     'value': original[0]['limits']['preview_chars']}])
    assert source_case.read_source() == original
    source_case.recover()


def test_completed_receipt_replays_after_source_update(source_case):
    packet = source_case.scenario.packet('profile', REGISTRY_CASES['first'])
    child, first = source_case.scenario.run('profile', packet)
    accepted = require_success(child, first, 'profile')
    cfg, _ = source_case.read_source()
    source_case.update(manifest_edits=[{'path': ['limits', 'preview_chars'],
                                      'value': cfg['limits']['preview_chars'] + 1}])
    before = source_case.snapshot('before-historical-replay')
    replay_child, replay = source_case.scenario.run('profile', packet)
    assert require_success(replay_child, replay, 'profile') == {**accepted, 'replayed': True}
    assert source_case.snapshot('after-historical-replay') == before
    assert not any(e['event'] == 'source_atomic_write_call' for e in replay_child.events)


def test_new_reviewed_intent_preserves_old_pending(source_case):
    source_case.interrupt()
    source_case.change_source('manifest')
    pending_before = source_case.pending.read_bytes()
    before = source_case.snapshot('before-new-intent')
    packet = deepcopy(source_case.packet)
    packet['request_id'] = CASES['new_intent_request']
    packet['expected_revision'] = project_revision(source_case.config)
    packet['profile']['runtime_resources']['hooks'][0]['installation_id'] = CASES['reviewed_installation']
    child, actual = source_case.scenario.run('profile', packet)
    value = require_success(child, actual, 'profile')
    assert value['project'] == REGISTRY_CASES['first']
    assert json.loads(source_case.scene.path.read_text()) == packet['profile']
    assert source_case.pending.read_bytes() == pending_before
    after = source_case.snapshot('after-new-intent')
    assert all(after[path] == before[path] for path in before
               if path not in (str(source_case.scene.path), str(source_case.scenario.registry)))
    assert_sentinel(source_case.scenario)
