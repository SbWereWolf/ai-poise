"""Concrete JSON receipt types at domain, publication and public acceptance."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from batch.helpers import request, result
from poise.common import PoiseError
from poise.infrastructure.artifact_factory import FileArtifactFactory
from poise.modules.verification.retention import AcceptanceManifest
from verification.retention_helpers import (
    FIXTURES, assert_bundle, file_item, manifest, source_file, start, verify_input,
)
from verification.retention_regression_helpers import (
    CONTROL_CASES, assert_control_types, assert_semantic_type_refusal,
    node_snapshot, malformed, semantic_snapshot,
)


@pytest.mark.parametrize('case', CONTROL_CASES)
def test_domain_rejects_equal_value_wrong_control_types(case):
    expected = json.loads((FIXTURES / 'strict_manifest.json').read_text())
    actual = malformed(expected, case)
    with pytest.raises(PoiseError, match='[Mm]anifest|receipt|schema|type'):
        AcceptanceManifest.validate(actual, expected)


def test_domain_accepts_independent_typed_manifest():
    expected = json.loads((FIXTURES / 'strict_manifest.json').read_text())
    AcceptanceManifest.validate(deepcopy(expected), expected)


@pytest.mark.parametrize('field', ['inputs', 'evidence', 'producer', 'extra', 'receipt-id'])
def test_domain_rejects_malformed_schema_shapes(field):
    expected = json.loads((FIXTURES / 'strict_manifest.json').read_text())
    actual = deepcopy(expected)
    if field in ('inputs', 'evidence'):
        actual[field] = {}
    elif field == 'producer':
        actual['inputs'][0]['producer'] = []
    elif field == 'extra':
        actual['extra'] = 'unknown'
    else:
        actual['receipt']['id'] = 1
    with pytest.raises(PoiseError, match='[Mm]anifest|receipt|schema|type'):
        AcceptanceManifest.validate(actual, expected)


@pytest.mark.parametrize('case', CONTROL_CASES)
def test_real_producer_wrong_control_types_are_rejected(project, tmp_path, monkeypatch, case):
    marker = tmp_path / 'consumer.txt'
    wheel = source_file(tmp_path)
    tools, context = start(project, marker)
    original = FileArtifactFactory.materialize
    published = []
    authenticated = []
    port = tools.runtime.retention.port
    original_read = port.read

    def read(task, reference):
        actual = original_read(task, reference)
        assert len(published) == 1
        path, content = published[0]
        assert reference['path'] == str(path)
        assert reference['digest'] == hashlib.sha256(content).hexdigest()
        assert path.read_bytes() == content
        assert actual == json.loads(content)
        assert_control_types(actual, case)
        authenticated.append((deepcopy(reference), deepcopy(actual)))
        return actual

    monkeypatch.setattr(port, 'read', read)

    def publish(factory, prepared):
        changed = []
        for item in prepared:
            try:
                value = json.loads(item.content)
            except (ValueError, UnicodeError):
                value = None
            if isinstance(value, dict) and value.get('schema') == 'acceptance-manifest-1':
                actual = malformed(value, case)
                item = replace(item, content=json.dumps(actual).encode())
                published.append((item.path, item.content))
            changed.append(item)
        return original(factory, tuple(changed))

    monkeypatch.setattr(FileArtifactFactory, 'materialize', publish)
    try:
        with pytest.raises(PoiseError) as refused:
            tools.invoke(request('verify', {
                'result': result(context), 'artifacts': [file_item(wheel)],
            }))
        assert_semantic_type_refusal(refused.value, case)
    finally:
        assert len(authenticated) == 1, 'no successful authenticated manifest readback'
        assert len(published) == 1, 'real manifest producer seam not exercised'
        path, content = published[0]
        assert path.read_bytes() == content
        assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(content).hexdigest()
        actual = json.loads(content)
        assert marker.read_text() == 'run\n', 'genuine consumer did not finish once'
        assert_control_types(actual, case)
        receipt = actual['receipt']
        assert receipt['id']
        assert receipt['actual_exit_code'] == 0 and receipt['passed'] == 1
        evidence = {item['kind']: item for item in actual['evidence']}
        assert set(evidence) == {'stdout', 'stderr', 'output'}
        assert Path(evidence['stdout']['path']).read_bytes() == (
            b'retained-bytes=00ff61636365707465642d776865656c800a\n'
        )
        assert Path(evidence['stderr']['path']).read_bytes() == b''
        assert Path(evidence['output']['path']).read_bytes() == b'wheel verified with accepted bytes\n'
        for fact in evidence.values():
            assert hashlib.sha256(Path(fact['path']).read_bytes()).hexdigest() == fact['digest']
        record = tools.runtime.task_queries.record('T1')
        assert record['status'] not in ('verified', 'accepted', 'completed')
        assert record['last_report'] is None


@pytest.mark.parametrize('case', CONTROL_CASES)
def test_public_accept_rejects_wrong_control_types_without_completion(project, tmp_path, monkeypatch, case):
    tools, context, done, _, marker = verify_input(project, tmp_path)
    path, valid = manifest(done, context)
    assert type(valid['receipt']['actual_exit_code']) is int
    assert type(valid['receipt']['passed']) is bool
    port = tools.runtime.retention.port
    original = port.read
    observations = []

    def read(task, reference):
        value = original(task, reference)  # Actual file, digest and confinement checks.
        assert reference['path'] == str(path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == reference['digest']
        observations.append(deepcopy(reference))
        return malformed(value, case)

    monkeypatch.setattr(port, 'read', read)
    before = semantic_snapshot(tools.runtime)
    record = deepcopy(tools.runtime.task_queries.record('T1'))
    files = node_snapshot(Path(context['task_root']))
    try:
        with pytest.raises(PoiseError) as refused:
            tools.invoke(request('accept', {}))
        assert_semantic_type_refusal(refused.value, case)
    finally:
        assert observations and observations[0] == done['acceptance_manifests'][0]
        assert marker.read_text() == 'run\n'
        assert node_snapshot(Path(context['task_root'])) == files
        assert tools.runtime.task_queries.record('T1') == record
        assert semantic_snapshot(tools.runtime) == before


def test_public_accept_typed_manifest_completes_without_rerunning_consumer(project, tmp_path, monkeypatch):
    tools, context, done, _, marker = verify_input(project, tmp_path)
    path, _ = manifest(done, context)
    port = tools.runtime.retention.port
    original = port.read
    observations = []

    def read(task, reference):
        value = original(task, reference)
        assert type(value['receipt']['actual_exit_code']) is int
        assert type(value['receipt']['passed']) is bool
        observations.append(reference)
        return deepcopy(value)

    monkeypatch.setattr(port, 'read', read)
    files = node_snapshot(Path(context['task_root']))
    completed = tools.invoke(request('accept', {}))
    assert completed['status'] == 'completed'
    assert observations and observations[0]['path'] == str(path)
    assert marker.read_text() == 'run\n'
    assert node_snapshot(Path(context['task_root'])) == files


@pytest.mark.parametrize('entry', ['receipt', 'bundle'])
@pytest.mark.parametrize('case', CONTROL_CASES)
def test_saved_receipt_and_bundle_reject_wrong_control_types(entry, case):
    from poise.application.acceptance_retention import AcceptanceRetention

    expected = json.loads((FIXTURES / 'strict_manifest.json').read_text())
    declaration = {
        'manifest_directory': 'artifacts',
        'files': [{
            'id': 'wheel', 'path': 'artifacts/wheel.whl',
            'digest': 'c' * 64,
            'producer': {'task_id': 'PRODUCER', 'commit': 'd' * 40},
            'input_ref': 'accepted-wheel', 'environment': 'ACCEPTED_WHEEL',
        }],
    }
    reference = {'path': '/task/artifacts/manifest.json', 'digest': '9' * 64}
    receipt = {
        'id': 'check', 'actual_exit_code': 0, 'passed': True,
        'retention_method': {'id': 'READ_WHEEL', 'artifact_inputs': declaration, 'outputs': []},
        'retention_definition_digest': 'f' * 64,
        'retention_tree': 'b' * 40, 'acceptance_manifest': reference,
    }
    observed = []

    class Port:
        def inputs(self, task, declared):
            assert task == {'id': 'T1'}
            assert declared.manifest_directory == 'artifacts'
            return deepcopy(expected['inputs'])

        def evidence(self, task, method, actual_receipt):
            assert actual_receipt == receipt
            return deepcopy(expected['evidence'])

        def read(self, task, received_reference):
            assert received_reference == reference
            observed.append(deepcopy(received_reference))
            return malformed(expected, case)

        def registered(self, task, paths):
            assert task == {'id': 'T1'} and paths == []
            return []

    owner = AcceptanceRetention(Port())
    with pytest.raises(PoiseError, match='[Mm]anifest|receipt|schema|type'):
        if entry == 'receipt':
            owner.validate_receipt({'id': 'T1'}, receipt, 'a' * 40)
        else:
            owner.validate_bundle({'id': 'T1'}, {
                'checks': [receipt], 'commit': 'a' * 40,
                'acceptance_manifests': [reference],
            })
    assert observed == [reference]


@pytest.mark.parametrize('case', CONTROL_CASES)
def test_producer_oracle_rejects_transport_failure_after_real_read(project, tmp_path, monkeypatch, case):
    from poise.infrastructure.acceptance_retention import RuntimeAcceptanceRetention

    original = RuntimeAcceptanceRetention.read
    observed = []

    def unrelated_digest_refusal(port, task, reference):
        actual = original(port, task, reference)
        assert_control_types(actual, case)
        observed.append(deepcopy(reference))
        raise PoiseError('Acceptance manifest digest changed: injected transport failure')

    with monkeypatch.context() as scoped:
        scoped.setattr(RuntimeAcceptanceRetention, 'read', unrelated_digest_refusal)
        with pytest.raises(AssertionError, match='no successful authenticated manifest readback'):
            test_real_producer_wrong_control_types_are_rejected(project, tmp_path, scoped, case)
    assert len(observed) == 1, 'actual original read/digest validation was not exercised'


def test_real_producer_typed_manifest_succeeds(project, tmp_path):
    tools, context, done, _, marker = verify_input(project, tmp_path)
    path, value = manifest(done, context)
    actual = tools.runtime.retention.port.read(tools.runtime.task_queries.record('T1'),
                                               done['acceptance_manifests'][0])
    assert actual == value
    assert type(actual['receipt']['actual_exit_code']) is int
    assert actual['receipt']['actual_exit_code'] == 0
    assert type(actual['receipt']['passed']) is bool
    assert actual['receipt']['passed'] is True
    assert hashlib.sha256(path.read_bytes()).hexdigest() == done['acceptance_manifests'][0]['digest']
    assert_bundle(done, context, done['commit'])
    assert marker.read_text() == 'run\n'
