"""Actual skills producer -> complete-result storage -> expanded agent consumer.

No native host event or model memory is simulated. Explicit continuation inputs
and separately confirmed retained text define the selected stateless protocol.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tests.skills.instruction_delivery_helpers import Scenario, body, fixture as old_fixture
from tests.runtime_services.session_tool_results_support import SessionScenario


FIXTURES = Path(__file__).parent / 'fixtures/context_delivery'


def literal(name):
    return json.loads((FIXTURES / name).read_text())


def fill(value, scenario):
    """Only substitute fixture-owned absolute addresses, never expected behavior."""
    replacements = {'$CONFIG': str(scenario.config), '$CHECKOUT': str(scenario.checkout),
                    '$POLICY': str(scenario.policy)}
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, dict):
        return {k: fill(v, scenario) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, scenario) for v in value]
    return value


@pytest.fixture
def delivery(project, tmp_path):
    scenario = Scenario(project, tmp_path, 'alpha')
    scenario.write('.agents/guides/skills.md', (FIXTURES / 'skills.md').read_bytes())
    scenario.write('.agents/guides/skills-detail.md', (FIXTURES / 'skills-detail.md').read_bytes())
    scenario.write('.agents/guides/unselected.md', b'UNRELATED_GUIDE_MUST_NOT_BE_DELIVERED\n')
    return scenario


def packet(scenario, previous=None, retained=()):
    request = fill(literal('request.json'), scenario)
    if previous is not None:
        request.update(previous=deepcopy(previous), retained=list(retained))
        request['context']['reason'] = 'continue'
    return request


def invoke(scenario, request):
    code, value = scenario.invoke(request)
    if (code == 2 and value == {'status': 'rejected', 'reason': 'Unknown skills operation'}):
        raise AssertionError('DELIVERY_UNSUPPORTED: the valid deliver operation is missing')
    return code, value


def successful(scenario, request):
    code, value = invoke(scenario, request)
    assert code == 0, value
    assert set(value) == {'schema', 'status', 'observation', 'delivery', 'receipt'}
    assert value['schema'] == 'poise-instruction-delivery-1'
    assert value['status'] == 'ready'
    return value


def metadata(records):
    return [{k: v for k, v in m.items() if k != 'content'} for m in records]


def assert_partition(value, delivered, reused):
    assert value['delivery']['materials'] == delivered
    assert value['delivery']['reused'] == metadata(reused)
    assert value['observation']['materials'] == sorted(
        [*delivered, *reused], key=lambda m: (m['kind'], m['id'], m['root'], m['path']))


def consumer(project, tmp_path):
    parent = tmp_path / 'consumer'
    parent.mkdir()
    scene = SessionScenario(project, parent)
    scene.policy['parsers'] = {'Instructions': {
        'parser': 'poise', 'format': 'work-json',
        'brief_template': 'Instructions: {status}', 'expanded_template': '{brief}',
        'expanded_fields': [{'path': ['delivery'], 'required': False,
                             'format': 'json', 'template': '{value}'},
                            {'path': ['error'], 'required': False,
                             'format': 'json', 'template': '{value}'}],
        'expanded_separator': '\n', 'brief': {'chars': 128, 'bytes': 256},
        'expanded': {'chars': 32000, 'bytes': 64000}, 'overflow': 'reject',
    }}
    scene.policy['display'].update(output_chars=48000, output_bytes=96000)
    scene.save_policy()
    return scene


def receive(scene, value, key, *, exit_code=0):
    """Actual machine capture followed by the common agent expanded-view CLI."""
    scene.out.write_text(json.dumps(value, ensure_ascii=False) + '\n')
    scene.err.write_bytes(b'')
    request = scene.request(request_id=key, tool={'kind': 'generic', 'name': 'Instructions'},
                            response=value, exit_code=exit_code)
    stored, saved = scene.capture(request, audience='machine')
    assert stored['response'] == value == saved['response']
    assert stored['exit_code'] == exit_code
    assert Path(stored['streams']['stdout']['path']).read_bytes() == scene.out.read_bytes()
    view = scene.success(scene.request('view.template.json', view='expanded'))
    assert view['exit_code'] == exit_code
    assert view['preview'].startswith('Instructions: ' + value['status'] + '\n')
    expected = value['delivery'] if 'delivery' in value else value['error']
    assert json.loads(view['preview'].split('\n', 1)[1]) == expected
    return view, stored


def test_initial_stage_and_selected_tool_reach_actual_agent_consumer(delivery, project, tmp_path):
    request = packet(delivery)
    value = successful(delivery, request)
    expected = literal('initial-materials.json')
    assert_partition(value, expected, [])
    assert value['receipt'] == fill(literal('receipt.json'), delivery)
    expected_observation = old_fixture('response-envelope.json')
    expected_observation.update(project='alpha', checkout=str(delivery.checkout),
                                instruction_root=str(delivery.checkout), materials=expected,
                                policy_sha256=hashlib.sha256(delivery.policy.read_bytes()).hexdigest())
    assert value['observation'] == expected_observation
    assert value['delivery'] == fill(literal('initial-delivery.json'), delivery)
    scene = consumer(project, tmp_path)
    view, _ = receive(scene, value, 'initial')
    assert 'UNRELATED_GUIDE_MUST_NOT_BE_DELIVERED' not in view['preview']
    guide = value['delivery']['materials'][-1]['content']
    assert guide == (FIXTURES / 'skills.md').read_text()
    assert len(guide.split()) <= 50
    assert '[Detailed prepare contract](skills-detail.md#prepare)' in guide
    assert '## Prepare' in (delivery.checkout / '.agents/guides/skills-detail.md').read_text()


def test_repeat_reduces_agent_bytes_and_keeps_complete_machine_sources(delivery, project, tmp_path):
    scene = consumer(project, tmp_path)
    first = successful(delivery, packet(delivery))
    initial, _ = receive(scene, first, 'first')
    repeated = successful(delivery, packet(delivery, first['receipt'], literal('retained.json')))
    current, machine = receive(scene, repeated, 'repeat')
    assert_partition(repeated, [], literal('initial-materials.json'))
    assert repeated['receipt'] == first['receipt']
    assert repeated['observation'] == first['observation']
    assert len(current['preview'].encode()) < len(initial['preview'].encode())
    assert len(json.dumps(repeated['delivery']).encode()) < len(json.dumps(first['delivery']).encode())
    assert machine['response']['observation']['materials'] == literal('initial-materials.json')
    for material in literal('initial-materials.json'):
        assert material['content'] not in current['preview']


@pytest.mark.parametrize('retained', [[], ['skill:core:instructions:.agents/skills/core/SKILL.md']])
def test_receipt_without_full_retention_delivers_the_complement(delivery, retained):
    first = successful(delivery, packet(delivery))
    value = successful(delivery, packet(delivery, first['receipt'], retained))
    records = literal('initial-materials.json')
    if retained:
        reused = [records[5]]
        delivered = [*records[:5], *records[6:]]
    else:
        delivered, reused = records, []
    assert_partition(value, delivered, reused)


@pytest.mark.parametrize('change', ['resume', 'compaction', 'context', 'generation', 'stage', 'paths'])
def test_current_context_and_scope_changes_refresh_instead_of_refusing(delivery, project, tmp_path, change):
    first = successful(delivery, packet(delivery))
    request = packet(delivery, first['receipt'], literal('retained.json'))
    if change in ('resume', 'compaction'):
        request['context']['reason'] = change
    elif change == 'context':
        request['context']['id'] = 'context-beta'
    elif change == 'generation':
        request['context']['generation'] = 2
    elif change == 'stage':
        request['stage']['id'] = 'code-inspection'
    else:
        request['paths'] = ['src/billing/invoice.py', 'docs/guide.md']
    value = successful(delivery, request)
    expected = literal('initial-materials.json')
    if change == 'stage':
        expected[6]['reasons'] = ['stage:code-inspection']
    if change == 'paths':
        expected = literal('paths-materials.json')
    assert_partition(value, expected, [])
    assert value['delivery']['context'] == request['context']
    assert value['delivery']['stage'] == request['stage']
    assert value['delivery']['paths'] == sorted(request['paths'])
    expected_receipt = fill(literal('receipt.json'), delivery)
    expected_receipt.update(context={k: request['context'][k] for k in ('id', 'generation')},
                            stage=request['stage'], paths=sorted(request['paths']))
    if change == 'paths':
        expected_receipt['materials'] = [{k: m[k] for k in ('kind', 'id', 'root', 'path', 'sha256')}
                                        for m in literal('paths-materials.json')]
    assert value['receipt'] == expected_receipt
    if change in ('resume', 'compaction'):
        view, _ = receive(consumer(project, tmp_path), value, change)
        assert json.loads(view['preview'].split('\n', 1)[1])['materials'] == expected


def test_changed_version_delivers_only_its_current_body(delivery):
    first = successful(delivery, packet(delivery))
    delivery.write('.agents/skills/billing/SKILL.md', body('changed-billing'))
    value = successful(delivery, packet(delivery, first['receipt'], literal('retained.json')))
    current = literal('initial-materials.json')
    current[4] = deepcopy(current[4])
    current[4].update(content=body('changed-billing').decode(),
                      sha256=hashlib.sha256(body('changed-billing')).hexdigest())
    assert_partition(value, [current[4]], [*current[:4], *current[5:]])
    assert value['receipt']['materials'][4]['sha256'] == current[4]['sha256']


@pytest.mark.parametrize('name,required', [('billing', True), ('audit', False), ('writing', True)])
def test_missing_material_is_not_reused_and_recovery_delivers_it(delivery, project, tmp_path, name, required):
    first = successful(delivery, packet(delivery))
    path = delivery.checkout / f'.agents/skills/{name}/SKILL.md'
    original = path.read_bytes()
    path.unlink()
    request = packet(delivery, first['receipt'], literal('retained.json'))
    code, value = invoke(delivery, request)
    assert code == (1 if required else 0)
    assert value['status'] == ('needs_inputs' if required else 'ready')
    records = literal('initial-materials.json')
    missing = next(m for m in records if m['id'] == name)
    missing.update(available=False, content=None, sha256=None)
    surviving = [m for m in literal('initial-materials.json') if m['id'] != name]
    assert_partition(value, [missing], surviving)
    expected = literal('missing.json')[name]
    assert value['delivery']['diagnostics'] == value['observation']['diagnostics'] == expected
    expected_receipt = fill(literal('receipt.json'), delivery)
    expected_receipt['materials'] = [m for m in expected_receipt['materials'] if m['id'] != name]
    assert value['receipt'] == expected_receipt
    if name == 'billing':
        view, machine = receive(consumer(project, tmp_path), value, 'needs-inputs', exit_code=1)
        visible = json.loads(view['preview'].split('\n', 1)[1])
        assert visible['diagnostics'] == literal('missing.json')['billing']
        assert visible['materials'] == [missing]
        assert machine['response'] == value
    path.write_bytes(original)
    retained = [key for key in literal('retained.json') if not key.startswith(f'skill:{name}:')]
    recovered = successful(delivery, packet(delivery, value['receipt'], retained))
    assert_partition(recovered, [m for m in literal('initial-materials.json') if m['id'] == name], surviving)
    assert recovered['receipt'] == fill(literal('receipt.json'), delivery)


def test_missing_tool_guide_is_an_addressed_required_input(delivery):
    (delivery.checkout / '.agents/guides/skills.md').unlink()
    code, value = invoke(delivery, packet(delivery))
    assert code == 1 and value['status'] == 'needs_inputs'
    assert value['delivery']['materials'][-1]['content'] is None
    assert value['delivery']['diagnostics'] == literal('missing-tool.json')


@pytest.mark.parametrize('case', literal('invalid-cases.json'), ids=lambda case: case['id'])
def test_malformed_inputs_have_exact_actionable_errors(delivery, project, tmp_path, case):
    request = packet(delivery)
    if case['previous']:
        request = packet(delivery, fill(literal('receipt.json'), delivery), literal('retained.json'))
    target = request
    for part in case['path'][:-1]:
        target = target[part]
    if case['action'] == 'remove':
        del target[case['path'][-1]]
    else:
        target[case['path'][-1]] = deepcopy(case['value'])
    code, value = invoke(delivery, request)
    assert code == 2
    assert value == literal('errors.json')[case['error']]
    if case['id'] == 'boolean-generation':
        view, machine = receive(consumer(project, tmp_path), value, 'rejected', exit_code=2)
        assert json.loads(view['preview'].split('\n', 1)[1]) == literal('errors.json')['context']['error']
        assert machine['response'] == literal('errors.json')['context']


def test_common_consumer_overflow_refuses_without_claiming_delivery(delivery, project, tmp_path):
    value = successful(delivery, packet(delivery))
    scene = consumer(project, tmp_path)
    scene.policy['parsers']['Instructions']['expanded'] = {'chars': 64, 'bytes': 64}
    scene.save_policy()
    request = scene.request(request_id='overflow', tool={'kind': 'generic', 'name': 'Instructions'},
                            response=value, exit_code=0)
    code, result = scene.direct(request, audience='agent')
    assert code == 2
    assert result['status'] == 'rejected'
    assert result['error']['code'] == 'budget_too_small'
    assert result['error']['field'] == 'expanded'
    assert result['error']['action'] == 'increase:budget'


def test_existing_consumer_projects_delivery_and_preserves_full_source(project, tmp_path):
    scene = consumer(project, tmp_path)
    value = literal('consumer-input.json')
    view, machine = receive(scene, value, 'consumer-guard')
    assert json.loads(view['preview'].split('\n', 1)[1]) == {'materials': ['AGENT_BODY']}
    assert 'MACHINE_FULL_SOURCE' not in view['preview']
    assert machine['response']['observation'] == {'content': 'MACHINE_FULL_SOURCE'}


def test_empty_stage_selection_and_no_tool_preserve_file_instructions(delivery):
    request = packet(delivery)
    request['stage']['skill_ids'] = []
    request['tool'] = None
    value = successful(delivery, request)
    assert_partition(value, old_fixture('billing-expected.json'), [])


def test_existing_consumer_preserves_declared_error_and_producer_exit(project, tmp_path):
    value = literal('errors.json')['context']
    view, machine = receive(consumer(project, tmp_path), value, 'rejected-guard', exit_code=2)
    assert json.loads(view['preview'].split('\n', 1)[1]) == literal('errors.json')['context']['error']
    assert machine['response'] == literal('errors.json')['context']


def test_existing_consumer_preserves_missing_input_and_producer_exit(project, tmp_path):
    value = literal('consumer-needs-inputs.json')
    view, machine = receive(consumer(project, tmp_path), value, 'missing-guard', exit_code=1)
    assert json.loads(view['preview'].split('\n', 1)[1]) == literal('consumer-needs-inputs.json')['delivery']
    assert machine['response'] == literal('consumer-needs-inputs.json')
