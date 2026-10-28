"""Check invocation travels through the same actual native ordered dispatcher."""
from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess
import sys

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
