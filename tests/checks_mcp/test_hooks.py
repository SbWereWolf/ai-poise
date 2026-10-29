"""Check invocation travels through the same actual native ordered dispatcher."""
from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from .hooks_support import hooks, native, native_peer


def test_native_pre_actual_run_and_matching_post_keep_order(case):
    chain = hooks(case)
    arguments = case.arguments()
    before = deepcopy(arguments)
    pre, _ = native(case, chain, 'PreToolUse', 'mcp__repo_checks__run', arguments)
    assert pre['systemMessage'] == 'seed|before|after'
    updated = pre['hookSpecificOutput']['updatedInput']
    assert updated['_invocation']
    assert {k: v for k, v in updated.items() if k != '_invocation'} == before
    peer = native_peer(case, chain)
    envelope, body = case.full_run(peer, arguments=updated)
    assert body['invocation']['id'] == updated['_invocation']
    post, _ = native(case, chain, 'PostToolUse', 'mcp__repo_checks__run', updated, response=envelope)
    assert post['systemMessage'] == 'seed|before|after'
    assert arguments == before
    case.assert_streams(case.record(body))


def test_native_preview_does_not_reserve_invocation(case):
    chain = hooks(case)
    arguments = case.arguments(preview=True)
    result, _ = native(case, chain, 'PreToolUse', 'mcp__repo_checks__run', arguments)
    updated = result.get('hookSpecificOutput', {}).get('updatedInput', arguments)
    assert '_invocation' not in updated
    peer = native_peer(case, chain)
    _, body = case.full_run(peer, arguments=updated)
    assert body['invocation']['id'] is None and not case.rows()


def test_wrong_post_does_not_release_live_native_run(case):
    case.child(mode='term')
    chain = hooks(case)
    pre, _ = native(case, chain, 'PreToolUse', 'mcp__repo_checks__run', case.arguments())
    arguments = pre['hookSpecificOutput']['updatedInput']
    peer = native_peer(case, chain)
    peer.call('held', 'run', arguments)
    case.started()
    # Genuine isolated native Post reaches the consumer with a distinct tool ID.
    native(case, chain, 'PostToolUse', 'mcp__repo_checks__run', arguments,
           key='different-native-tool', response={'isError': True, 'content': []})
    _, preview = case.cancel_outcome(peer, 'preview', identity=arguments['_invocation'], preview=True)
    assert preview['status'] == 'planned'
    assert preview['target']['invocation_id'] == arguments['_invocation']
    assert not [row for row in case.rows() if row['event'] == 'TERM']
    case.release()
    envelope, completed = peer.outcome('held')
    assert completed['result']['status'] == 'passed'
    native(case, chain, 'PostToolUse', 'mcp__repo_checks__run', arguments, response=envelope)


def test_yielded_bash_keeps_actual_cli_owned_run(case):
    case.child(mode='term')
    chain = hooks(case)
    packet = case.root / 'cli-request.json'
    packet.write_text(json.dumps(case.scene.request(fresh=True)))
    argv = [sys.executable, '-B', '-m', 'poise', 'checks', '--config', str(case.scene.config),
            '--checkout', str(case.scene.checkout), '--policy', str(case.scene.policy_path),
            '--view', 'full', '--transport-policy', str(case.policy_path)]
    args = {'command': shlex.join(argv) + ' < ' + shlex.quote(str(packet))}
    pre, environment = native(case, chain, 'PreToolUse', 'Bash', args)
    updated = pre['hookSpecificOutput']['updatedInput']
    out, err = (case.root / 'bash.stdout').open('wb'), (case.root / 'bash.stderr').open('wb')
    process = subprocess.Popen(['bash', '-c', updated['command']], cwd=case.scene.checkout,
                               env=environment, stdout=out, stderr=err, start_new_session=True)
    case.external.append((process, out, err))
    case.started()
    native(case, chain, 'PostToolUse', 'Bash', updated,
           response={'session_id': 'isolated-yield', 'exit_code': None})
    peer = native_peer(case, chain)
    _, preview = case.cancel_outcome(peer, 'preview', preview=True)
    assert preview['status'] == 'planned'
    _, cancelled = case.cancel_outcome(peer, identity=preview['target']['invocation_id'])
    assert cancelled['status'] == 'cancelled'
    assert process.wait(timeout=20) == 1
    assert [row['event'] for row in case.rows()] == ['STARTED', 'TERM']


def test_unrelated_bash_preserves_original_input_and_response(case):
    chain = hooks(case)
    arguments = {'command': 'printf isolated-unrelated-text'}
    pre, _ = native(case, chain, 'PreToolUse', 'Bash', arguments)
    assert pre['systemMessage'] == 'seed|before|after'
    assert pre.get('hookSpecificOutput', {}).get('updatedInput', arguments) == arguments
    assert not case.rows()


def test_known_raw_and_wrapped_commands_have_addressed_mcp_recipe(case):
    chain = hooks(case)
    command = shlex.join(case.scene.profile()['argv'])
    for key, text in [('raw', command), ('wrapped', command + ' | cat')]:
        actual, _ = native(case, chain, 'PreToolUse', 'Bash', {'command': text}, key=key)
        result = actual['hookSpecificOutput']
        assert result['permissionDecision'] == 'deny'
        assert 'sample' in result['permissionDecisionReason']
        assert 'mcp__repo_checks__run' in result['permissionDecisionReason']
    assert not case.rows()


def test_weak_script_reference_is_only_a_warning(case):
    chain = hooks(case)
    command = 'printf %s ' + shlex.quote(str(case.scene.profile()['argv'][2]))
    actual, _ = native(case, chain, 'PreToolUse', 'Bash', {'command': command})
    result = actual.get('hookSpecificOutput', {})
    assert result.get('permissionDecision', 'allow') == 'allow'
    assert result.get('updatedInput', {'command': command})['command'] == command
    assert 'sample' in result['additionalContext']
    assert not case.rows()


def test_late_terminal_post_cannot_replace_new_live_invocation(case):
    case.child(marker='second', mode='term', name='second')
    chain = hooks(case)
    peer = native_peer(case, chain)
    first_pre, _ = native(case, chain, 'PreToolUse', 'mcp__repo_checks__run', case.arguments(), key='first')
    first_args = first_pre['hookSpecificOutput']['updatedInput']
    first_envelope, _ = case.full_run(peer, 'first-run', first_args)
    native(case, chain, 'PostToolUse', 'mcp__repo_checks__run', first_args, key='first', response=first_envelope)
    second_pre, _ = native(case, chain, 'PreToolUse', 'mcp__repo_checks__run', case.arguments(name='second'), key='second')
    second_args = second_pre['hookSpecificOutput']['updatedInput']
    peer.call('second-run', 'run', second_args)
    case.started('second')
    native(case, chain, 'PostToolUse', 'mcp__repo_checks__run', first_args, key='first', response=first_envelope)
    _, preview = case.cancel_outcome(peer, 'current-preview', identity=second_args['_invocation'], preview=True)
    assert preview['status'] == 'planned' and preview['target']['invocation_id'] == second_args['_invocation']
    assert not [row for row in case.rows('second') if row['event'] == 'TERM']
    case.release('second')
    envelope, completed = peer.outcome('second-run')
    assert completed['result']['status'] == 'passed'
    native(case, chain, 'PostToolUse', 'mcp__repo_checks__run', second_args, key='second', response=envelope)


def test_later_pre_stop_is_not_fabricated_as_a_started_check(case):
    chain = hooks(case, after_action='deny')
    pre, _ = native(case, chain, 'PreToolUse', 'mcp__repo_checks__run', case.arguments())
    assert pre['hookSpecificOutput']['permissionDecision'] == 'deny'
    identity = pre['hookSpecificOutput']['updatedInput']['_invocation']
    assert identity, 'CHECK_PRE_PRODUCER_NOT_REACHED'
    peer = native_peer(case, chain)
    _, result = case.cancel_outcome(peer, identity=identity)
    assert result['status'] == 'not_active'
    assert result['termination']['graceful_requested'] is False
    assert result['termination']['force_requested'] is False
    assert not case.rows()


@pytest.mark.parametrize('axis', ['caller', 'project', 'checkout', 'selection', 'options'])
def test_prepared_reference_rejects_other_valid_run_binding(case, monkeypatch, axis):
    """The real consumer must check a good Pre reference, not just its shape.

    Every changed binding has a healthy untracked control. The prepared original
    remains executable after refusal; two real held scopes must stay untouched.
    """
    from .support import Arrangement, literal
    from tests.named_checks.support import beta_project, git, write_json

    case.child(marker='other-valid', name='other-valid')
    case.child(marker='protected-live', mode='hold', name='protected-live')
    case.child(marker='foreign-live', mode='hold', name='foreign-live')
    for name in ['protected-live', 'foreign-live']:
        case.scene.profile(name, execution_timeout_seconds=60)
    case.scene.save()
    selected = None
    beta = None
    if axis == 'project':
        configured, beta_root = beta_project(case.scene.project, case.root)
        beta_parent = beta_root / 'admission-arrangement'
        beta_parent.mkdir()
        beta = Arrangement(configured, beta_parent, monkeypatch)
        case.cleanup_callbacks.append(beta.close)
        beta.child(marker='other-valid', name='other-valid')
        selected = {'config': str(beta.scene.config), 'checkout': str(beta.scene.checkout),
                    'policy': str(beta.scene.policy_path)}
        code, proof, _ = beta.scene.invoke(beta.scene.request(fresh=True))
        assert (code, proof['status']) == (0, 'passed'), 'VALID_PROJECT_PRECONDITION'
        write_json(case.root / 'valid-project-precondition.json', proof)
    elif axis == 'checkout':
        other = case.root / 'registered-copy'
        git(case.scene.checkout, 'worktree', 'add', '-b', 'codex/f13-admission-copy', str(other))
        case.cleanup_callbacks.append(lambda: git(case.scene.checkout, 'worktree', 'remove', str(other)))
        selected = {'config': str(case.scene.config), 'checkout': str(other),
                    'policy': str(case.scene.policy_path)}
        code, proof, _ = case.scene.invoke(case.scene.request(fresh=True), checkout=other)
        assert (code, proof['status']) == (0, 'passed'), 'VALID_REGISTERED_COPY_PRECONDITION'
        assert proof['items'][0]['steps'][0]['record']['cwd'] == str(other)
        write_json(case.root / 'valid-copy-precondition.json', proof)

    chain = hooks(case)
    original = case.arguments()
    prepared, _ = native(case, chain, 'PreToolUse', 'mcp__repo_checks__run', original,
                         key='prepared-original')
    good = prepared['hookSpecificOutput']['updatedInput']
    assert good['_invocation'], 'ADMISSION_PRE_PRODUCER_NOT_REACHED'
    assert {k: v for k, v in good.items() if k != '_invocation'} == original
    owner = native_peer(case, chain)
    foreign = case.peer('admission-foreign', environment=case.environment)
    foreign.initialize()

    # Both independent scopes really execute before the mismatched reference.
    live_pre, _ = native(case, chain, 'PreToolUse', 'mcp__repo_checks__run',
                         case.arguments(name='protected-live'), key='protected-live')
    live_args = live_pre['hookSpecificOutput']['updatedInput']
    owner.call('protected-live', 'run', live_args)
    own_child = case.started('protected-live')
    foreign.call('foreign-live', 'run', case.arguments(name='foreign-live'))
    foreign_child = case.started('foreign-live')
    assert own_child['pid'] != foreign_child['pid']

    consumer = foreign if axis == 'caller' else owner
    bad = deepcopy(good)
    if axis in ('project', 'checkout'):
        bad['binding'] = selected
    elif axis == 'selection':
        bad['selection'] = case.scene.selection('other-valid')
    elif axis == 'options':
        bad['options']['fresh'] = False

    # Same valid consumer/binding/options, without the foreign Pre reference:
    # proving this control must not depend on invalid config/caller rejection.
    healthy = deepcopy(bad)
    healthy.pop('_invocation')
    consumer.call('valid-consumer-control', 'run', healthy)
    control_envelope, control = consumer.outcome('valid-consumer-control')
    assert control_envelope['isError'] is False and control['result']['status'] == 'passed'
    control_case = beta if axis == 'project' else case
    control_record = control_case.record(control)
    expected_checkout = selected['checkout'] if selected is not None else str(case.scene.checkout)
    assert control_record['cwd'] == expected_checkout
    assert control_case.rows('other-valid' if axis == 'selection' else 'subject')

    _, foreign_preview = case.cancel_outcome(foreign, 'foreign-scope-preview', preview=True)
    assert foreign_preview['status'] == 'planned'
    identities = [good['_invocation'], live_args['_invocation'], foreign_preview['target']['invocation_id']]
    metadata_root = case.scene.state / case.policy['invocation']['directory']
    paths = [metadata_root / identity / case.policy['invocation']['record_file'] for identity in identities]
    before = {str(path): path.read_bytes() for path in paths}
    assert all(json.loads(raw) for raw in before.values()), 'ADMISSION_HEALTHY_BINDINGS_NOT_REACHED'
    case_before = {name: deepcopy(case.rows(name)) for name in
                   ['subject', 'other-valid', 'protected-live', 'foreign-live']}
    beta_before = None if beta is None else deepcopy(beta.rows())

    consumer.call('mismatched-reference', 'run', bad)
    envelope, refused = consumer.outcome('mismatched-reference')
    expected = literal('admission-refusal.json')
    assert envelope['isError'] is expected['isError']
    assert refused['result']['status'] == expected['status']
    assert refused['result']['error']['code'] == expected['code'], 'MISMATCH_ADMISSION_NOT_REACHED'
    assert refused['result']['error']['field'] == expected['field'], 'MISMATCH_ADMISSION_NOT_REACHED'
    assert refused['result']['error']['cause'] and refused['result']['error']['recovery']
    assert refused['result']['items'] == []
    delivered = [json.loads(line) for line in consumer.input_path.read_text().splitlines()]
    attacked = [row for row in delivered if row.get('id') == 'mismatched-reference']
    assert len(attacked) == 1 and attacked[0]['params']['arguments'] == bad
    assert bad['_invocation'] == good['_invocation']
    assert {str(path): path.read_bytes() for path in paths} == before
    assert {name: case.rows(name) for name in case_before} == case_before
    if beta is not None:
        assert beta.rows() == beta_before
    write_json(case.root / ('admission-' + axis + '.json'), {
        'axis': axis, 'consumer_pid': consumer.process.pid,
        'owner_pid': owner.process.pid, 'foreign_pid': foreign.process.pid,
        'prepared': good, 'delivered': attacked[0], 'refusal': refused,
        'metadata_before': {name: raw.decode() for name, raw in before.items()},
        'metadata_after': {str(path): path.read_text() for path in paths},
        'child_events': {name: case.rows(name) for name in case_before}})
    for peer, request in [(owner, 'owned-scope-ping'), (foreign, 'foreign-scope-ping')]:
        peer.request(request, 'ping', {})
        assert peer.response(request)['result'] == {}

    # Reuse the actual unchanged prepared reference: a blanket refusal cannot pass.
    owner.call('matching-original', 'run', good)
    matched_envelope, matched = owner.outcome('matching-original')
    assert matched_envelope['isError'] is False and matched['result']['status'] == 'passed'
    assert matched['invocation']['id'] == good['_invocation']
    assert len([row for row in case.rows() if row['event'] == 'STARTED']) == (
        len([row for row in case_before['subject'] if row['event'] == 'STARTED']) + 1)
    native(case, chain, 'PostToolUse', 'mcp__repo_checks__run', good,
           key='prepared-original', response=matched_envelope)
    case.release('protected-live'); case.release('foreign-live')
    own_envelope, own_result = owner.outcome('protected-live')
    _, foreign_result = foreign.outcome('foreign-live')
    assert own_result['result']['status'] == foreign_result['result']['status'] == 'passed'
    native(case, chain, 'PostToolUse', 'mcp__repo_checks__run', live_args,
           key='protected-live', response=own_envelope)
