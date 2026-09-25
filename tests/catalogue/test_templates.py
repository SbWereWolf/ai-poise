"""TDD contracts: independent process packs and exact task instantiation."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from poise.modules.foundation.errors import PoiseError
from poise.modules.goal_config.domain import GoalTypeDefinition
from poise.modules.catalogue.domain import TaskBlueprint

ROOT = Path(__file__).resolve().parents[2]
GOALS = ('development','test_development','verification','review','design','analysis',
         'profiling','environment_diagnostics','environment_remediation','documentation',
         'task_planning','sprint_planning','integration')


def documents(goal):
    base=ROOT/'config/catalogue'
    return (json.loads((base/'process-templates'/f'{goal}.json').read_text()),
            json.loads((base/'task-templates'/f'{goal}.json').read_text()))


@pytest.mark.parametrize('goal',GOALS)
def test_independent_process_is_valid_and_has_complete_section_contract(goal):
    process,template=documents(goal)
    parsed=GoalTypeDefinition.parse(process).data
    assert parsed['goal_type']==goal
    assert all(s['instruction'] and s['required_sections'] for s in parsed['stages'] if s['handler']!='publish')
    assert all(s['read_only'] for s in parsed['stages'] if s['handler']=='inspect')
    assert 'extends' not in parsed and 'inherit' not in parsed
    assert TaskBlueprint.parse(template).goal_type==goal


def test_every_normative_node_is_present_without_new_engine():
    source=json.loads((ROOT/'docs/architecture-design/data/stage-library-map.json').read_text())['nodes']
    expected={(n['goal_type'],n['stage']) for n in source}
    actual={(g,s['id']) for g in GOALS for s in documents(g)[0]['stages']}
    assert actual==expected


def test_every_feedback_target_is_declared_for_user_rework():
    edges=json.loads((ROOT/'docs/architecture-design/reference/feedback-map.json').read_text())['edges']
    for edge in edges:
        stages={s['id']:s for s in documents(edge['goal_type'])[0]['stages']}
        assert edge['to_stage'] in stages[edge['from_stage']]['rework_targets']


def tiny():
    # The template is explicit data, not a source of implicit optional parameters.
    return {'schema':'task-blueprint-1','goal_type':'custom','parameters':
            {'identity':'text','membership':'nullable_text','goal':'text','requirements':'strings',
             'dod':'strings','methods':'list','method_inputs':'list','contract':'object','evidence':'object',
             'stage_contracts':'list','decomposition':'object'},
            'task':{'id':{'$input':'identity'},'sprint_id':{'$input':'membership'},'goal_type':'custom',
                    'goal':{'$input':'goal'},'requirements':{'$input':'requirements'},
                    'definition_of_done':{'$input':'dod'},'methods':{'$input':'methods'},
                    'method_inputs':{'$input':'method_inputs'},
                    'checks':{'draft':[]},'artifact_requirements':[],
                    'content_contract':{'$input':'contract'},'evidence_plan':{'$input':'evidence'},
                    'stage_contracts':{'$input':'stage_contracts'},
                    'decomposition':{'$input':'decomposition'}}}


def values():
    return {'identity':'T','membership':None,'goal':'Write a precise note',
            'requirements':['R1'],'dod':['D1'],'methods':[],'method_inputs':[],
            'contract':{'sections':[],'routes':[],'requirements':[]},
            'evidence':{'draft':{'subject_methods':{},'arguments':[],'review_arguments':[]}},
            'stage_contracts':[{'stage_id':'draft','allowed_paths':[],
                'entry_requirements':[],'exit_requirements':[]}],
            'decomposition':{'kind':'ordinary','phases':[
                {'stage':'draft','skills':['workflow'],'areas':[]}],
                'integration':None}}


def decomposition_policy():
    return {'skills':[{'id':'workflow','class':'meta','responsibility':None}],
            'areas':[{'path':'src/**','responsibility':'application'}]}


def process():
    return {'goal_type':'custom','worktree_required':True,'route':{'entry':'draft'},
            'content_contract':{'sections':[],'routes':[],'requirements':[]},
            'benefit':{'git_categories':[],'sections':['note']},'stages':[{
            'id':'draft','handler':'produce', "role": "executor",'instruction':'Write a note and report.',
            'transitions':{'complete':None},'rework_targets':['draft'],
            'read_only':True,'allowed_paths':[],'normalization':'strip',
            'sections':{'note':'Write note here.'},'required_sections':['note'],'artifact_requirements':[]}]}


def test_typed_instantiation_preserves_exact_command_and_does_not_mutate_template():
    source=tiny();before=deepcopy(source)
    created=TaskBlueprint.parse(source).instantiate(
        values(),process(),[],decomposition_policy()
    )
    assert created['id']=='T' and created['sprint_id'] is None
    assert source==before
    created['requirements'].append('R2')
    assert values()['requirements']==['R1']


@pytest.mark.parametrize('change',[lambda p:p.pop('membership'),lambda p:p.update(extra='guess'),
                                  lambda p:p.update(identity=12),lambda p:p.update(requirements='R')])
def test_missing_extra_or_wrong_parameter_is_rejected_without_fallback(change):
    p=values();change(p)
    with pytest.raises(PoiseError):TaskBlueprint.parse(tiny()).instantiate(
        p,process(),[],decomposition_policy()
    )


def test_unknown_placeholder_is_rejected():
    raw=tiny();raw['task']['goal']={'$input':'undeclared'}
    with pytest.raises(PoiseError):TaskBlueprint.parse(raw)


def test_exact_method_reference_is_validated_by_existing_task_owner():
    raw=tiny();raw['task']['checks']['draft']=['NO_SUCH_METHOD']
    with pytest.raises(PoiseError):TaskBlueprint.parse(raw).instantiate(
        values(),process(),[],decomposition_policy()
    )


def test_mismatched_process_identity_is_not_reclassified():
    p=process();p['goal_type']='other'
    with pytest.raises(PoiseError):TaskBlueprint.parse(tiny()).instantiate(
        values(),p,[],decomposition_policy()
    )
