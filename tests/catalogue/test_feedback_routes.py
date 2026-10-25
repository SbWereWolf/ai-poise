"""Run each explicitly documented feedback edge, not combinatorial fault mixtures."""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from itertools import repeat
from multiprocessing import get_context
from pathlib import Path
import json
import pytest
from conftest import WorkPoise as Poise, bind_task_requirements, git, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'examples'))
EDGES=json.loads((ROOT/'config/catalogue/reference.json').read_text())['feedback_edges']


def _run_edge(root,edge):
    from catalogue_walkthrough import run
    return edge,run(root/edge['id'],edge['goal_type'],'feedback',edge['id'])


@pytest.mark.parametrize('edge_batch', [EDGES[:17], EDGES[17:]], ids=['first', 'second'])
def test_feedback_edges_return_to_work_and_finish_same_task(tmp_path, edge_batch):
    # Runtime telemetry may fork; never fork a multithreaded test worker.
    with ProcessPoolExecutor(max_workers=8, mp_context=get_context('spawn')) as pool:
        cases=pool.map(_run_edge,repeat(tmp_path),edge_batch)
        for edge,result in cases:
            case=edge['id']
            assert result['task_status']=='completed',case
            assert case in result['traversed_feedback_edges'],case
            assert result['ready_artifacts']>=1,case
            assert result['open_findings_at_completion']==0,case


def _call(actor, operation, inputs):
    return WorkTools(actor).invoke({'operation': operation, 'input': inputs, 'messages': []})


def _boot(actor, task=None, decision=None, target=None):
    return _call(actor, 'bootstrap', {
        'task': task, 'decision': decision,
        'feedback': 'Controlled fixture selects the declared ordinary correction target.' if target else None,
        'rework_stage': target,
    })


def _submit(actor, context, work, text, method_additions=None):
    result = deepcopy(context['result_template'])
    result['sections'] = {name: text for name in result['sections']}
    result['stage_work'] = work
    result['method_additions'] = [] if method_additions is None else method_additions
    return _call(actor, 'verify', {'result': result, 'artifacts': []})


def _transfer(sender, receiver, task_id, decision='continue', target=None):
    state = _call(sender, 'show', {'queries': [{'id': 'state', 'kind': 'task'}]})['results'][0]['value']
    released = _call(sender, 'release', {
        'task_id': task_id, 'request_id': f"direct-phase-{state['version']}-release",
        'expected_version': state['version'], 'reason': 'Transfer controlled fixture to its distinct participant.',
    })
    assert released['status'] == 'ownership_released'
    selected = _boot(receiver, {'id': task_id})
    return selected if decision is None else _boot(receiver, decision=decision, target=target)


def _plan_text(actor, plan):
    value = _call(actor, 'show', {'queries': [{
        'id': 'plan', 'kind': 'section', 'name': 'accepted_plan',
        'stage': plan, 'submission': None, 'range': None,
    }]})['results'][0]['value']
    return value['text']


def _inspection(findings=None, decisions=None):
    return {
        'coverage': 'Controlled reviewer inspects the actual result and original finding.',
        'findings': [] if findings is None else findings,
        'resolution_decisions': [] if decisions is None else decisions,
    }


def _finding():
    return {'id': 'F1', 'subject': 'Current phase result',
            'description': 'The result lacks its required evidence.', 'evidence': 'Evidence is absent in iteration one.'}


def _resolution(identifier):
    return {'id': identifier, 'finding_id': 'F1',
            'description': 'The ordinary phase writer supplies the missing evidence for the original finding.',
            'evidence': f'The current report contains evidence for {identifier}.'}


def _development_phase(project, phase):
    """Test-owned legal phase isolates existing backend behavior from the missing16-node configuration."""
    plan, plan_review, execute, execution_review = phase
    stages = []
    for name, purpose, handler, role, sections, transitions, targets in (
        (plan, 'design', 'produce', 'executor', {'accepted_plan': 'Record the approved plan.'},
         {'complete': plan_review}, [plan]),
        (plan_review, 'observation', 'inspect', 'reviewer', {'report': 'Inspect the plan.'},
         {'clear': execute, 'changes_requested': plan}, [plan, plan_review]),
        (execute, 'execution', 'produce', 'executor', {'report': 'Execute the accepted plan.'},
         {'complete': execution_review}, [execute]),
        (execution_review, 'observation', 'inspect', 'reviewer', {'report': 'Inspect the execution.'},
         {'clear': None, 'changes_requested': execute}, [execute, execution_review]),
    ):
        stages.append({
            'id': name, 'purpose': purpose, 'criteria_authority': {'propose': [], 'decide': []},
            'handler': handler, 'role': role, 'instruction': 'Execute only the declared controlled phase.',
            'read_only': True, 'allowed_paths': [], 'normalization': 'strip',
            'sections': sections, 'required_sections': list(sections), 'artifact_requirements': [],
            'transitions': transitions, 'rework_targets': targets,
        })
    # This fixture's real GREEN checks double.py; its execution owner must be allowed
    # to deliver that declared change surface. Inspector and design scopes stay read-only.
    stages[2]['read_only'] = False
    stages[2]['allowed_paths'] = ['src/**']
    if execute == 'test_implementation':
        stages[2]['sections']['test_registry'] = 'Describe the actual current registry.'
    workflow = {
        'route': {'entry': plan}, 'goal_type': 'development', 'worktree_required': True,
        'stages': stages, 'benefit': {'git_categories': [], 'sections': ['accepted_plan', 'report']},
        'content_contract': {'sections': [], 'routes': [], 'requirements': []},
    }
    write_json(project['root'] / 'config/workflows/development.json', workflow)
    cfg = deepcopy(project['cfg'])
    cfg['automatic_checks'] = []
    write_json(project['config_path'], cfg)
    (project['app'] / 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    tests = project['app'] / 'tests'
    tests.mkdir()
    (tests / '__init__.py').write_text('')
    (tests / 'test_double.py').write_text(
        'import unittest\nfrom src.double import double\n'
        'class Regression(unittest.TestCase):\n'
        '    def test_double(self):\n        self.assertEqual(double(2), 4)\n'
        '        self.assertEqual(double(0), 0)\n'
    )
    git(project['app'], 'add', '.')
    git(project['app'], 'commit', '-m', 'Prepare the real GREEN fixture')
    green = deepcopy(next(method for method in project['task']['methods'] if method['id'] == 'GREEN'))
    green['verification_plan']['green_stages'] = [execute, execution_review]
    task = deepcopy(project['task'])
    task.update(
        id='DIRECT', methods=[green],
        requirements=['double(2) returns 4.', 'double(0) returns 0.'],
        method_inputs=[{'method_id': 'GREEN', 'repository_inputs': ['tests'], 'future_outputs': [],
                        'reference_profile': {'runner': 'unittest', 'parser': 'discover-start-directory', 'version': 1}}],
        checks={stage['id']: (['GREEN'] if stage['id'] in (execute, execution_review) else []) for stage in stages},
        definition_of_done=['The accepted plan is retained and the original inspector accepts the correction.'],
        evidence_plan={stage['id']: {'subject_methods': {}, 'arguments': [], 'review_arguments': []} for stage in stages},
        stage_contracts=[{'stage_id': stage['id'], 'allowed_paths': list(stage['allowed_paths']), 'entry_requirements': [], 'exit_requirements': [],
                         'criteria': {'requirements': ['Retain the accepted plan and inspect actual evidence.'],
                                      'definition_of_ready': ['The declared controlled phase is available.'],
                                      'definition_of_done': ['The corresponding result is independently checked.']}}
                        for stage in stages],
        decomposition={'kind': 'ordinary', 'phases': [
            {'stage': stage['id'], 'skills': ['task-domain'], 'areas': []} for stage in stages], 'integration': None},
        content_contract={'sections': [], 'routes': [], 'requirements': []},
    )
    bind_task_requirements(task, project['requirements_registry'])
    if execute == 'test_implementation':
        task['executable_obligations'] = ['requirements[0]', 'requirements[1]']
    executor = Poise(project['config_path'], 'FIXTURE-EXECUTOR')
    reviewer = Poise(project['config_path'], 'FIXTURE-REVIEWER')
    context = _boot(executor, task)
    assert context['stage'] == plan
    return executor, reviewer, context, green


def _classify(green, execute, inspect, *, revision):
    return {'request_id': f'classify-current-green-{revision}', 'expected_revision': revision,
            'operations': [{'kind': 'replace', 'method_id': 'GREEN', 'registration': {
                'method': green, 'stages': [execute, inspect],
                'evidence_kind': 'executable_test', 'covers': ['requirements[0]', 'requirements[1]'],
            }}], 'executable_obligations': ['requirements[0]', 'requirements[1]']}


PHASES = (
    ('verification_planning', 'verification_planning_inspection', 'test_implementation', 'test_inspection'),
    ('implementation_design', 'implementation_design_inspection', 'implementation', 'implementation_inspection'),
    ('documentation_planning', 'documentation_planning_inspection', 'documentation', 'documentation_inspection'),
)


@pytest.mark.parametrize('phase', PHASES, ids=['test', 'code', 'documentation'])
def test_development_execution_repair_preserves_accepted_plan_and_original_finding(project, phase):
    plan, plan_review, execute, inspect = phase
    executor, reviewer, ctx, green = _development_phase(project, phase)
    assert _submit(executor, ctx, {}, 'ACCEPTED PLAN')['status'] == 'verified'
    ctx = _transfer(executor, reviewer, 'DIRECT')
    assert ctx['stage'] == plan_review
    assert ctx['session'] == 'FIXTURE-REVIEWER'
    assert _submit(reviewer, ctx, _inspection(), 'The plan is accepted.')['stage_outcome'] == 'clear'
    ctx = _transfer(reviewer, executor, 'DIRECT')
    additions = _classify(green, execute, inspect, revision=0) if execute == 'test_implementation' else None
    assert _submit(executor, ctx, {}, 'Initial execution report.', additions)['status'] == 'verified'
    ctx = _transfer(executor, reviewer, 'DIRECT')
    rejected = _submit(reviewer, ctx, _inspection([_finding()]), 'Missing evidence.')
    assert rejected['stage_outcome'] == 'changes_requested'
    assert [finding['id'] for finding in rejected['feedback']['open_findings']] == ['F1']
    ctx = _transfer(reviewer, executor, 'DIRECT', 'rework', execute)
    for number, verdict in ((1, 'rejected'), (2, 'accepted')):
        assert ctx['stage'] == execute
        assert _plan_text(executor, plan) == 'ACCEPTED PLAN'
        proposal = _submit(executor, ctx, {'resolutions': [_resolution(f'R{number}')]}, f'Evidence for R{number}.')
        assert [finding['id'] for finding in proposal['feedback']['open_findings']] == ['F1']
        assert [item['id'] for item in proposal['feedback']['pending_resolutions']] == [f'R{number}']
        ctx = _transfer(executor, reviewer, 'DIRECT')
        assert ctx['stage'] == inspect
        assert ctx['session'] == 'FIXTURE-REVIEWER'
        decided = _submit(reviewer, ctx, _inspection(decisions=[{
            'resolution_id': f'R{number}', 'decision': verdict, 'reason': f'Actual evidence is {verdict}.',
        }]), f'Independent decision for R{number}.')
        assert _plan_text(reviewer, plan) == 'ACCEPTED PLAN'
        if verdict == 'rejected':
            assert decided['stage_outcome'] == 'changes_requested'
            ctx = _transfer(reviewer, executor, 'DIRECT', 'rework', execute)
    assert decided['stage_outcome'] == 'clear'
    assert decided['feedback']['open_findings'] == []
    assert decided['feedback']['pending_resolutions'] == []
    assert [(item['finding_id'], item['id']) for item in decided['feedback']['resolutions']] == [('F1', 'R1'), ('F1', 'R2')]
    assert [(item['resolution_id'], item['decision']) for item in decided['feedback']['decisions']] == [('R1', 'rejected'), ('R2', 'accepted')]
    assert decided['feedback']['findings'][0]['stage'] == inspect
    assert _call(reviewer, 'accept', {})['status'] == 'completed'


def test_development_design_rejection_returns_to_the_same_design_writer(project):
    plan, plan_review, execute, _ = PHASES[0]
    executor, reviewer, ctx, _ = _development_phase(project, PHASES[0])
    _submit(executor, ctx, {}, 'Initial plan.')
    ctx = _transfer(executor, reviewer, 'DIRECT')
    _submit(reviewer, ctx, _inspection([_finding()]), 'Plan evidence is missing.')
    ctx = _transfer(reviewer, executor, 'DIRECT', 'rework', plan)
    assert ctx['stage'] == plan
    _submit(executor, ctx, {'resolutions': [_resolution('PLAN-R1')]}, 'CORRECTED ACCEPTED PLAN')
    ctx = _transfer(executor, reviewer, 'DIRECT')
    assert ctx['stage'] == plan_review
    accepted = _submit(reviewer, ctx, _inspection(decisions=[{
        'resolution_id': 'PLAN-R1', 'decision': 'accepted', 'reason': 'The corrected plan is executable.',
    }]), 'Corrected design accepted.')
    assert accepted['stage_outcome'] == 'clear'
    ctx = _transfer(reviewer, executor, 'DIRECT')
    assert ctx['stage'] == execute
    assert _plan_text(executor, plan) == 'CORRECTED ACCEPTED PLAN'


def test_development_registry_refusal_recovers_to_the_ordinary_writer_without_planning(project):
    plan, _, execute, inspect = PHASES[0]
    executor, reviewer, ctx, green = _development_phase(project, PHASES[0])
    _submit(executor, ctx, {}, 'ACCEPTED PLAN')
    ctx = _transfer(executor, reviewer, 'DIRECT')
    _submit(reviewer, ctx, _inspection(), 'Plan accepted.')
    ctx = _transfer(reviewer, executor, 'DIRECT')
    _submit(executor, ctx, {}, 'Actual GREEN exists but current executable coverage is unclassified.')
    ctx = _transfer(executor, reviewer, 'DIRECT')
    with pytest.raises(PoiseError, match='текущий GREEN executable-test'):
        _submit(reviewer, ctx, _inspection(), 'Coverage is still insufficient.')
    with pytest.raises(PoiseError, match='Нет точного доступного failed check batch текущего результата'):
        _boot(reviewer, decision='rework', target=plan)
    refused = _call(reviewer, 'show', {'queries': [{'id': 'state', 'kind': 'task'}]})['results'][0]['value']
    assert refused['stage'] == inspect
    assert refused['status'] == 'active'
    assert _plan_text(reviewer, plan) == 'ACCEPTED PLAN'
    ctx = _boot(reviewer, decision='rework', target=execute)
    assert ctx['stage'] == execute
    ctx = _transfer(reviewer, executor, 'DIRECT', decision=None)
    assert ctx['stage'] == execute
    assert _plan_text(executor, plan) == 'ACCEPTED PLAN'
    incomplete = _classify(green, execute, inspect, revision=0)
    incomplete['operations'][0]['registration']['covers'] = ['requirements[0]']
    assert _submit(executor, ctx, {}, 'One of two obligations is covered.', incomplete)['status'] == 'verified'
    ctx = _transfer(executor, reviewer, 'DIRECT')
    with pytest.raises(PoiseError) as missing:
        _submit(reviewer, ctx, _inspection(), 'The second obligation is still uncovered.')
    assert str(missing.value) == "Текущие GREEN executable-test не покрывают ['requirements[1]']"
    current = _call(reviewer, 'show', {'queries': [{'id': 'registry', 'kind': 'verification_registry'}]})['results'][0]['value']
    assert current['executable_obligations'] == ['requirements[0]', 'requirements[1]']
    ctx = _boot(reviewer, decision='rework', target=execute)
    ctx = _transfer(reviewer, executor, 'DIRECT', decision=None)
    repaired = _submit(executor, ctx, {}, 'Both obligations covered.', _classify(green, execute, inspect, revision=1))
    assert repaired['status'] == 'verified'
    ctx = _transfer(executor, reviewer, 'DIRECT')
    assert ctx['stage'] == inspect
    assert ctx['session'] == 'FIXTURE-REVIEWER'
    final = _submit(reviewer, ctx, _inspection(), 'Current executable coverage independently inspected.')
    assert final['stage_outcome'] == 'clear'
    assert _plan_text(reviewer, plan) == 'ACCEPTED PLAN'
    assert _call(reviewer, 'accept', {})['status'] == 'completed'
