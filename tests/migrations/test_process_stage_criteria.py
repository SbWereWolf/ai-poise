"""Explicit process-format cutover; literal output owns the oracle."""
import json

import pytest

from poise.infrastructure.process_stage_criteria import process_source


def test_typed_task_moves_only_the_workflow_field():
    row = {'id': 'T', 'status': 'active', 'version': 7,
           'metadata': json.dumps({'process': {'goal_type': 'build'},
                                   'contract': {'goal': 'Keep result'},
                                   'restart_history': [{'process': {'old': True}}]})}
    source = process_source(row)
    assert json.loads(source['metadata']) == {
        'workflow': {'goal_type': 'build'},
        'contract': {'goal': 'Keep result'},
        'restart_history': [{'process': {'old': True}}],
    }
    assert source['id'] == 'T'
    assert source['version'] == 7
    assert source['status'] == 'active'
    assert 'process' in json.loads(row['metadata'])


def test_untyped_draft_keeps_its_exact_metadata_bytes():
    row = {'status': 'newborn', 'metadata': '{ "newborn": {"draft": {}, "ready": false} }'}
    assert process_source(row) == row


@pytest.mark.parametrize('field', ['newborn', 'draft'])
@pytest.mark.parametrize('value', [None, [], 'invalid', 1, True])
def test_newborn_source_rejects_malformed_required_objects(field, value):
    metadata = {'newborn': {'draft': {}, 'ready': False}}
    if field == 'newborn':
        metadata['newborn'] = value
    else:
        metadata['newborn']['draft'] = value
    with pytest.raises(ValueError, match=field):
        process_source({'status': 'newborn', 'metadata': json.dumps(metadata)})


@pytest.mark.parametrize('metadata', [{}, {'newborn': {}}])
def test_newborn_source_requires_both_objects(metadata):
    with pytest.raises(ValueError, match='process-format'):
        process_source({'status': 'newborn', 'metadata': json.dumps(metadata)})


@pytest.mark.parametrize('metadata', [
    {'workflow': {'goal_type': 'build'}},
    {'workflow': {}, 'process': {}},
    {'process': None},
    {'process': []},
    {},
])
def test_typed_source_requires_the_explicit_process_format(metadata):
    with pytest.raises(ValueError, match='process-format'):
        process_source({'status': 'active', 'metadata': json.dumps(metadata)})


def test_archived_setup_readiness_does_not_block_stage_migration(project):
    from tests.migrations.project_criteria_helpers import historical_project_owned, run_migration

    case = historical_project_owned(project)
    receipt = json.loads(case['setup_receipt'].read_text())
    receipt['result']['readiness'] = {'old_optional_probe': 'not_used_by_migration'}
    case['setup_receipt'].write_text(json.dumps(receipt))
    code, result, stderr = run_migration(case, fresh=True)
    assert code == 0, (result, stderr)
    assert result['status'] == 'migrated'


def _process_case(project, drafts, legacy_receipt, current_definitions):
    from tests.migrations.project_criteria_helpers import historical_project_owned
    from tests.migrations.project_criteria_draft_helpers import (
        rows, replace_metadata, fresh_backup, draft_case, metadata_digest,
    )

    case = draft_case(project) if drafts else historical_project_owned(project)
    for task_id, row in rows(case).items():
        metadata = json.loads(row[7])
        if 'workflow' in metadata:
            metadata['process'] = metadata.pop('workflow')
            replace_metadata(case, task_id, metadata)
            for resource in case['packet']['mapping']:
                if resource.get('task_id') == task_id and resource['kind'] == 'newborn_draft':
                    resource['expected_metadata_digest'] = metadata_digest(metadata)
    case['packet']['schema'] = 'task-stage-criteria-process-migration-1'
    current_paths = []
    if current_definitions != 'legacy':
        config = json.loads(case['config'].read_text())
        for resource in case['packet']['mapping']:
            if resource['kind'] != 'workflow_definition':
                continue
            path = case['config'].parent / config['workflows'][resource['goal_type']]
            workflow = json.loads(path.read_text())
            for stage, mapped in zip(workflow['stages'], resource['stages'], strict=True):
                stage['purpose'] = mapped['purpose']
                stage['criteria_authority'] = mapped['criteria_authority']
            path.write_text(' \n' + json.dumps(workflow, indent=3) + '\n \t')
            resource['expected_revision'] = metadata_digest(workflow)
            current_paths.append(path)
            if current_definitions == 'mixed_definitions':
                break
    if not legacy_receipt:
        settings = json.loads(case['settings'].read_text())
        settings.pop('receipt')
        case['settings'].write_text(json.dumps(settings))
        case['setup_receipt'].unlink()
    fresh_backup(case)
    case['current_definition_bytes'] = {path: path.read_bytes() for path in current_paths}
    return case


@pytest.mark.parametrize('drafts', [False, True])
@pytest.mark.parametrize('legacy_receipt', [False, True])
@pytest.mark.parametrize('current_definitions', ['legacy', 'current', 'mixed_definitions'])
def test_public_process_cutover_preserves_task_history(project, drafts, legacy_receipt, current_definitions):
    from tests.migrations.project_criteria_helpers import observe, run_migration

    case = _process_case(project, drafts, legacy_receipt, current_definitions)
    before = observe(case)['task']
    code, result, stderr = run_migration(case, fresh=True)
    assert code == 0, (result, stderr)
    assert result['status'] == 'migrated'
    assert {path: path.read_bytes() for path in case['current_definition_bytes']} == case['current_definition_bytes']
    after = observe(case)['task']
    for table in ('events', 'submissions', 'evidence', 'execution', 'methods', 'proofs', 'content'):
        assert after[table] == before[table]
    for old, new in zip(before['tasks'], after['tasks'], strict=True):
        assert new[:7] == old[:7]
        metadata = json.loads(new[7])
        assert 'process' not in metadata
        if 'process' in json.loads(old[7]):
            assert 'workflow' in metadata
    completed = observe(case)
    replay_code, replay, replay_stderr = run_migration(case, fresh=True)
    assert replay_code == 0, (replay, replay_stderr)
    assert replay['status'] == 'migrated'
    assert replay['replayed'] is True
    assert observe(case) == completed


@pytest.mark.parametrize('fault', ['purpose', 'authority', 'partial', 'mixed_stages', 'inventory', 'drift'])
def test_current_definition_conflict_rejects_without_changes(project, fault):
    from tests.migrations.project_criteria_helpers import observe, run_migration
    from tests.migrations.project_criteria_draft_helpers import metadata_digest

    case = _process_case(project, False, False, 'current')
    config = json.loads(case['config'].read_text())
    resource = next(item for item in case['packet']['mapping'] if item['kind'] == 'workflow_definition')
    path = case['config'].parent / config['workflows'][resource['goal_type']]
    workflow = json.loads(path.read_text())
    if fault == 'purpose':
        workflow['stages'][0]['purpose'] = 'execution'
    elif fault == 'authority':
        workflow['stages'][0]['criteria_authority'] = {'propose': [], 'decide': []}
    elif fault == 'partial':
        workflow['stages'][0].pop('criteria_authority')
    elif fault == 'mixed_stages':
        workflow['stages'][0].pop('purpose')
        workflow['stages'][0].pop('criteria_authority')
    elif fault == 'inventory':
        resource['stages'].reverse()
    else:
        workflow['stages'][0]['instruction'] += ' Changed after observation.'
    if fault != 'inventory':
        path.write_text(json.dumps(workflow))
        if fault != 'drift':
            resource['expected_revision'] = metadata_digest(workflow)
    before = observe(case)
    code, result, stderr = run_migration(case, fresh=True)
    assert code == 2, (result, stderr)
    assert result['status'] == 'rejected'
    if fault == 'drift':
        assert 'revision mismatch' in result['reason']
    assert observe(case) == before
