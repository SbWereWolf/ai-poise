"""Reviewed consumer contract, with literal independent material expectations."""
from copy import deepcopy
import hashlib

import pytest

from tests.skills.portable_delivery_support import (
    address, canonical, cli, continuation_request, execute,
    expected_frame, literal, long_request, read_request, rejected,
    replace_body, request, selected_records, successful,
)
from tests.skills.test_context_instruction_delivery import (
    consumer, delivery, fill, literal as legacy_literal, packet, receive,
)
from tests.skills.instruction_delivery_helpers import fixture as legacy_fixture


class BudgetDeliveryMissing(AssertionError):
    """Raised only after a valid control and the exact old protocol refusal."""


def test_bounded_file_entry_after_valid_unbounded_control(delivery):
    original = packet(delivery)
    code, control = cli(original, delivery)
    assert code == 0
    assert control['schema'] == 'poise-instruction-delivery-1'
    materials = legacy_literal('initial-materials.json')
    expected_observation = legacy_fixture('response-envelope.json')
    expected_observation.update(
        project='alpha', checkout=str(delivery.checkout),
        instruction_root=str(delivery.checkout), materials=materials,
        policy_sha256=hashlib.sha256(delivery.policy.read_bytes()).hexdigest(),
    )
    assert control['observation'] == expected_observation
    assert control['delivery'] == fill(legacy_literal('initial-delivery.json'), delivery)
    assert control['receipt'] == fill(legacy_literal('receipt.json'), delivery)
    bounded = {**original, 'budget': {'max_bytes': 65536}, 'continuation': None}
    code, value = cli(bounded, delivery)
    if code == 2 and value == literal('old-budget-refusal.json'):
        raise BudgetDeliveryMissing('PORTABLE_DELIVERY_BUDGET_UNSUPPORTED')
    assert code == 0, value
    assert value['schema'] == 'poise-portable-instruction-result-1'
    assert value['status'] == 'complete'
    assert value['observation'] == expected_observation
    assert value['receipt'] == control['receipt']
    assert value['delivery']['materials'] == materials
    assert value['delivery']['reused'] == value['delivery']['remaining'] == []
    assert value['delivery']['continuation'] is None
    assert len(canonical(value['delivery'])) <= 65536
    # An existing selected concise guide supplies its explicit detailed address.
    assert materials[-1]['content'] == (delivery.checkout / '.agents/guides/skills.md').read_text()
    assert '## Prepare' in (delivery.checkout / '.agents/guides/skills-detail.md').read_text()


def test_bounded_producer_reaches_existing_machine_and_agent_consumers(delivery, project, tmp_path):
    bounded = {**packet(delivery), 'budget': {'max_bytes': 6000}, 'continuation': None}
    code, value = cli(bounded, delivery)
    assert code == 0 and value['status'] == 'partial'
    assert value['observation']['materials'] == legacy_literal('initial-materials.json')
    assert value['delivery']['materials'] == legacy_literal('initial-materials.json')[1:]
    assert value['delivery']['remaining'][0]['key'] == 'rule:AGENTS.md:checkout:AGENTS.md'
    assert len(canonical(value['delivery'])) <= 6000
    scene = consumer(project, tmp_path)
    view, stored = receive(scene, value, 'portable-bounded')
    assert stored['response']['observation']['materials'] == legacy_literal('initial-materials.json')
    assert view['exit_code'] == 0


@pytest.fixture(params=['api', 'cli'])
def entry(request):
    return request.param


def test_initial_exact_frame_receipt_and_complete_observation(entry):
    value = successful(request(), entry)
    assert value['status'] == 'complete'
    assert value['delivery'] == literal('complete-frame.json')
    assert value['receipt'] == literal('receipt.json')


@pytest.mark.parametrize('indices', [[], [1], [0, 1, 2]])
def test_only_explicit_current_same_version_confirmation_reuses(entry, indices):
    packet = request()
    first = successful(packet, entry)
    keys = literal('keys.json')
    repeated = continuation_request(first, packet, [keys[i] for i in indices])
    value = successful(repeated, entry)
    complement = [i for i in (0, 1, 2) if i not in indices]
    assert value['delivery'] == expected_frame(repeated, complement, indices)
    assert value['receipt'] == literal('receipt.json')


@pytest.mark.parametrize('change', ['id', 'generation', 'resume', 'compaction', 'stage', 'area'])
def test_context_stage_or_area_refreshes_full_text(entry, change):
    packet = request()
    first = successful(packet, entry)
    repeated = continuation_request(first, packet, literal('keys.json'))
    if change in ('resume', 'compaction'):
        repeated['context']['reason'] = change
    elif change == 'id':
        repeated['context']['id'] = 'new-current-context'
    elif change == 'generation':
        repeated['context']['generation'] = 2
    elif change == 'stage':
        repeated['stage']['id'] = 'review'
    else:
        repeated['selection']['paths'] = ['docs/guide.md']
        for record in repeated['selection']['materials']:
            record['scopes'] = ['docs/guide.md']
    value = successful(repeated, entry)
    expected = expected_frame(repeated)
    if change == 'area':
        expected['paths'] = ['docs/guide.md']
        for record in expected['materials']:
            record['scopes'] = ['docs/guide.md']
    assert value['delivery'] == expected


def test_changed_version_delivers_exact_changed_body(entry):
    packet = request()
    first = successful(packet, entry)
    repeated = continuation_request(first, packet, literal('keys.json'))
    replace_body(repeated, 1, 'New rules.\n')
    value = successful(repeated, entry)
    expected = expected_frame(repeated, [1], [0, 2])
    expected['materials'][0].update(
        content='New rules.\n', sha256=hashlib.sha256(b'New rules.\n').hexdigest(),
    )
    assert value['delivery'] == expected
    assert value['receipt']['materials'][1]['sha256'] == hashlib.sha256(b'New rules.\n').hexdigest()


def test_receipt_and_unconfirmed_retained_keys_never_imply_first_delivery(entry):
    packet = request()
    packet['retained'] = literal('keys.json')
    assert successful(packet, entry)['delivery'] == literal('complete-frame.json')


def test_exact_complete_budget_boundary_and_navigation_refusal(entry):
    packet = request()
    budget = len(canonical(literal('complete-frame.json')))
    packet['budget']['max_bytes'] = budget
    assert successful(packet, entry)['delivery'] == literal('complete-frame.json')
    packet['budget']['max_bytes'] = 1
    rejected(packet, 'small-budget', entry)
    # At one byte below complete, either explicit partial navigation fits or
    # its honest budget refusal applies. Complete/clipped success cannot fit.
    packet['budget']['max_bytes'] = budget - 1
    if entry == 'api':
        value = execute(packet)
    else:
        code, value = cli(packet)
        assert code in (0, 2)
    if value['status'] == 'rejected':
        assert value == literal('errors.json')['small-budget']
    else:
        assert value['status'] == 'partial'
        assert len(canonical(value['delivery'])) < budget
        assert value['delivery']['remaining']


def test_whole_body_packing_skips_long_body_and_preserves_full_machine_source(entry):
    packet = long_request()
    value = successful(packet, entry)
    frame = value['delivery']
    assert set(frame) == set(literal('frame-fields.json'))
    assert frame['status'] == value['status'] == 'partial'
    assert frame['materials'] == selected_records([1, 2])
    assert frame['reused'] == []
    assert len(frame['remaining']) == 1
    left = frame['remaining'][0]
    assert set(left) == {'key', 'required', 'sha256', 'address', 'reason'}
    assert left['key'] == literal('keys.json')[0]
    assert left['required'] is True and left['reason'] == 'budget'
    assert left['sha256'] == hashlib.sha256(literal('long-body.json')['content'].encode()).hexdigest()
    assert isinstance(left['address'], str) and left['address']
    assert isinstance(frame['continuation'], str) and frame['continuation']
    assert successful(packet, entry) == value
    assert len(canonical(value['observation'])) > packet['budget']['max_bytes']


def test_continuation_replays_until_confirmed_then_finishes_via_addressed_text(entry):
    packet = long_request()
    first = successful(packet, entry)
    again = continuation_request(first, packet, [])
    again['continuation'] = first['delivery']['continuation']
    replay = successful(again, entry)
    assert replay['delivery']['materials'] == selected_records([1, 2])
    assert replay['delivery']['remaining'] == first['delivery']['remaining']
    confirmed = continuation_request(first, packet, literal('keys.json')[1:])
    confirmed['continuation'] = first['delivery']['continuation']
    current = successful(confirmed, entry)
    assert current['delivery']['materials'] == []
    assert current['delivery']['reused'] == [
        {k: v for k, v in m.items() if k != 'content'} for m in selected_records([1, 2])
    ]
    read = read_request(confirmed, current['delivery']['remaining'][0]['address'])
    body = successful(read, entry)['delivery']
    assert body['content'].encode() == literal('long-body.json')['content'].encode()
    assert body['complete_material'] is True
    # This next retained assertion is supplied explicitly by the consumer only
    # after comparing the full addressed text, not inferred from the receipt.
    finished = continuation_request(current, confirmed, literal('keys.json'))
    finished['continuation'] = current['delivery']['continuation']
    done = successful(finished, entry)
    assert done['status'] == 'complete'
    assert done['delivery']['materials'] == done['delivery']['remaining'] == []
    assert done['delivery']['continuation'] is None


@pytest.mark.parametrize('change', ['source', 'reason', 'metadata', 'id', 'generation', 'stage', 'paths', 'budget'])
def test_changed_binding_rejects_continuation_and_refresh_is_available(entry, change):
    packet = long_request()
    first = successful(packet, entry)
    again = continuation_request(first, packet, [])
    again['continuation'] = first['delivery']['continuation']
    if change == 'source':
        replace_body(again, 0, 'Updated body.\n')
    elif change == 'reason':
        again['context']['reason'] = 'compaction'
    elif change == 'metadata':
        again['selection']['materials'][0]['reasons'] = ['updated-selection']
    elif change == 'id':
        again['context']['id'] = 'new-context'
    elif change == 'generation':
        again['context']['generation'] = 2
    elif change == 'stage':
        again['stage']['id'] = 'inspection'
    elif change == 'paths':
        again['selection']['paths'] = ['docs/guide.md']
        for record in again['selection']['materials']:
            record['scopes'] = ['docs/guide.md']
    else:
        again['budget']['max_bytes'] = 5001
    rejected(again, 'stale-continuation', entry)
    again['continuation'] = None
    assert successful(again, entry)['delivery']['reused'] == []


@pytest.mark.parametrize('required', [True, False])
def test_unavailable_remaining_diagnostic_and_source_restoration(entry, required):
    packet = request()
    material = packet['selection']['materials'][1]
    material.update(available=False, content=None, sha256=None, required=required)
    category = 'required' if required else 'recommended'
    diagnostic = literal('missing-' + category + '.json')
    packet['selection']['diagnostics'] = [diagnostic]
    packet['selection']['status'] = 'needs_inputs' if required else 'ready'
    value = successful(packet, entry)
    frame = value['delivery']
    assert value['status'] == ('needs_inputs' if required else 'complete')
    assert frame['diagnostics'] == [diagnostic]
    assert frame['remaining'] == literal('remaining-' + category + '.json')
    assert frame['materials'] == selected_records([0, 2])
    assert frame['continuation'] is None
    assert successful(request(), entry)['delivery'] == literal('complete-frame.json')


def readable_packet():
    packet = request()
    replace_body(packet, 1, literal('read-body.json')['content'])
    return packet


@pytest.mark.parametrize('case', literal('ranges.json'))
def test_exact_addressed_full_and_utf8_crlf_ranges(entry, case):
    packet = readable_packet()
    read = read_request(packet, address(packet, entry=entry), case['range'])
    value = successful(read, entry)
    expected = literal('read-frame.json')
    expected.update(range=case['range'], content=case['content'], complete_material=case['complete'])
    assert value['status'] == 'read'
    assert value['delivery'] == expected
    assert value['receipt']['materials'][1]['sha256'] == expected['sha256']


@pytest.mark.parametrize('selection_range', literal('invalid-ranges.json'))
def test_addressed_bad_ranges_are_exact_refusals(entry, selection_range):
    packet = readable_packet()
    rejected(read_request(packet, address(packet, entry=entry), selection_range), 'range', entry)


def test_ranges_reconstruct_literal_full_text_without_implicit_retention(entry):
    packet = readable_packet()
    ref = address(packet, entry=entry)
    chunks = []
    for first, last in [(1, 1), (2, 2), (3, 3)]:
        read = read_request(packet, ref, dict(unit='lines', start=first, end=last))
        value = successful(read, entry)
        assert value['delivery']['complete_material'] is False
        chunks.append(value['delivery']['content'])
    assert ''.join(chunks).encode() == literal('read-body.json')['content'].encode()
    assert successful(packet, entry)['delivery']['reused'] == []


def test_exact_read_budget_admission_and_empty_byte_range(entry):
    packet = readable_packet()
    ref = address(packet, entry=entry)
    read = read_request(packet, ref)
    read['budget']['max_bytes'] = len(canonical(literal('read-frame.json')))
    assert successful(read, entry)['delivery'] == literal('read-frame.json')
    read['budget']['max_bytes'] -= 1
    rejected(read, 'small-range', entry)
    read['range'] = dict(unit='bytes', start=0, end=0)
    assert successful(read, entry)['delivery']['content'] == ''


@pytest.mark.parametrize('change', ['hash', 'unavailable', 'context', 'stage', 'paths', 'unknown'])
def test_address_binding_validation_precedes_read_admission(entry, change):
    packet = readable_packet()
    read = read_request(packet, address(packet, entry=entry))
    if change == 'hash':
        replace_body(read, 1, 'Changed.\n')
    elif change == 'unavailable':
        read['selection']['materials'][1].update(available=False, content=None, sha256=None)
        read['selection']['status'] = 'needs_inputs'
        read['selection']['diagnostics'] = [literal('missing-required.json')]
    elif change == 'context':
        read['context']['generation'] = 2
    elif change == 'stage':
        read['stage']['id'] = 'review'
    elif change == 'paths':
        read['selection']['paths'] = ['docs/guide.md']
        for record in read['selection']['materials']:
            record['scopes'] = ['docs/guide.md']
    else:
        read['address'] = 'not-an-issued-address'
    rejected(read, 'stale-address', entry)


def test_empty_source_is_delivered_whole_without_lost_material(entry):
    packet = request()
    replace_body(packet, 1, '')
    value = successful(packet, entry)
    assert value['delivery']['materials'][1]['content'] == ''
    assert value['delivery']['materials'][1]['sha256'] == hashlib.sha256(b'').hexdigest()
    assert value['delivery']['remaining'] == []


@pytest.mark.parametrize('field,bad,error', [
    ('schema', 'future-schema', 'schema'), ('context', None, 'context'),
    ('stage', None, 'stage'), ('retained', None, 'retained'),
    ('budget', None, 'budget'), ('budget', {'max_bytes': True}, 'budget'),
    ('budget', {'max_bytes': 0}, 'budget'), ('continuation', 1, 'continuation'),
    ('previous', {}, 'previous'), ('selection', None, 'selection'),
])
def test_malformed_inputs_have_actionable_exact_errors(entry, field, bad, error):
    packet = request()
    packet[field] = bad
    rejected(packet, error, entry)


@pytest.mark.parametrize('change', ['duplicate', 'wrong-hash', 'wrong-status', 'wrong-diagnostic', 'root', 'kind'])
def test_normalized_selection_is_validated_before_delivery(entry, change):
    packet = request()
    material = packet['selection']['materials'][1]
    if change == 'duplicate':
        packet['selection']['materials'].insert(1, deepcopy(material))
    elif change == 'wrong-hash':
        material['sha256'] = 'b' * 64
    elif change == 'wrong-status':
        packet['selection']['status'] = 'needs_inputs'
    elif change == 'wrong-diagnostic':
        packet['selection']['diagnostics'] = [{'code': 'invented'}]
    elif change == 'root':
        material['root'] = 'other'
    else:
        material['kind'] = 'other'
    rejected(packet, 'selection', entry)


@pytest.mark.parametrize('field', ['context', 'stage', 'budget', 'continuation'])
def test_missing_required_fields_are_not_null_defaults(entry, field):
    packet = request()
    del packet[field]
    rejected(packet, 'request', entry)


def test_unknown_field_and_foreign_receipt_are_rejected(entry):
    packet = request()
    packet['unexpected'] = None
    rejected(packet, 'request', entry)
    packet = request()
    packet['previous'] = literal('receipt.json')
    packet['previous']['project'] = 'foreign-consumer'
    rejected(packet, 'previous', entry)
