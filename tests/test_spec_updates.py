"""Небольшие проверки трёх согласованных изменений текста, не продуктовая приёмка."""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]/'docs/spec-data'


def load(name):
    return json.loads((ROOT/name).read_text())


def test_all_profiles_accept_only_artifact_paths():
    for profile in load('process-matrices.json')['profiles']:
        assert 'artifact_declarations' not in profile['sections']
        for stage in profile['stages']:
            if stage['kind']!='mechanical_gate': assert 'artifact_paths' in stage['pre_submit']


def test_development_keeps_planned_test_references():
    scenario=next(s for s in load('stateful-scenarios.json')['scenarios'] if s['id']=='E2E-DEV')
    for e in scenario['events_clean']:
        if 'payload' in e and 'test_registry' in e['payload']:
            registry=e['payload']['test_registry']
            assert registry[0]['method_by_stage']['test_implementation']=='TEST_RED'
            assert registry[0]['method_by_stage']['implementation']=='TEST_GREEN'


def test_noncommand_expectations_match_input():
    scenario=next(s for s in load('stateful-scenarios.json')['scenarios'] if s['id']=='E2E-VER')
    for b in scenario['method_branches']:
        if b['id'] in ('logical_only_prepare','inspection_prepare','logical_rejected_by_review'):
            e=next(e for e in b['events'] if e['action']=='verify_prepare' and e.get('node')=='execution')
            assert len(e['payload']['manual_evidence'])==1
            assert 'manual_proposals=1' in e['expected'] and 'command_receipts=0' in e['expected']
        if b['id']=='logical_rejected_by_review':
            e=next(e for e in b['events'] if e['action']=='verify_prepare' and e.get('node')=='self_inspection')
            assert e['payload']['inspection_verdict']['verdict']=='rework_required'


def test_live_config_and_all_cancellation_scopes_are_canonical():
    docs=Path(__file__).resolve().parents[1]/'docs'
    requirements=(docs/'governance/requirements.md').read_text()
    goal_config=(docs/'configuration/goal-config.md').read_text()
    sprints=(docs/'workflows/sprints.md').read_text()
    batch=(docs/'workflows/batch-work.md').read_text()
    library=(docs/'architecture/library-api.md').read_text()
    combined='\n'.join((requirements,goal_config,sprints,batch,library))

    assert 'Stage iteration отдельно фиксирует project/execution/tool/hook snapshot' not in requirements
    assert 'Изменение **project execution config** по-прежнему останавливает текущую задачу' not in goal_config
    assert '`force_close`' not in combined
    assert '`cancel_tasks`' in sprints
    assert '"action":"cancel"' in sprints
    assert 'standalone Task' in batch
    assert 'Sprint целиком' in library
    assert 'отдельные worktree' in requirements
    assert 'optimistic' in requirements
    assert 'config_hash' in requirements
    assert 'диагност' in requirements
