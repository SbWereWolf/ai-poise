"""Actual configured forwarding entry, genuine saved service bytes and native owner."""
from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess

import pytest

from poise.modules.result_views.tool_results import ToolResultFailure, ToolResultPolicy
from tests.hook_transport.architecture_support import Chain
from tests.hook_transport.session_output_support import configure_chain, scene_for_chain
from tests.runtime_services.session_tool_results_support import cases


def configured_entry(project, tmp_path):
    chain = Chain(project, tmp_path, 'PUBLIC_ENTRY_SAVED_VIEWS')
    scene = scene_for_chain(chain, project, tmp_path)
    script = tmp_path / 'public-entry.sh'
    script.write_text('set -eu\n'
                      'test "$1" = --command\n'
                      'test "$2" = tool-result\n'
                      'shift 2\n'
                      'exec ' + shlex.quote(scene.python) + ' -B -m poise tool-result "$@"\n')
    prefix = ['bash', str(script), '--command', 'tool-result']
    scene.policy['service_entry'] = {'argv': prefix, 'cwd': str(scene.checkout)}
    scene.save_policy()
    configure_chain(chain, scene)
    event = scene.native(cases()['bash-success']['response'], key='original-producer')['event']
    event['session_id'] = chain.session
    chain.invoke(event, event_name='PostToolUse')
    full = scene.service('full')
    original = {str(path): path.read_bytes() for path in Path(full['full_path']).parent.iterdir()
                if path.is_file()}
    last = Path(full['full_path']).parent.parent.parent / scene.policy['session']['last']
    assert json.loads(last.read_bytes()) == {'uuid': full['run_id']}
    return chain, scene, prefix, full, original, last, last.read_bytes()


def service_event(chain, scene, prefix, operation):
    argv = [*prefix, '--config', str(scene.config), '--checkout', str(scene.checkout),
            '--policy', str(scene.policy_path), '--audience', 'agent']
    request = {'schema': 'poise-tool-result-request-1', 'operation': operation}
    process = subprocess.run(argv, input=json.dumps(request).encode(), env=scene.env,
                             cwd=scene.checkout, capture_output=True, check=False)
    assert process.returncode == 0 and process.stderr == b'', (process.returncode, process.stderr)
    answer = json.loads(process.stdout)
    event = scene.native({'output': process.stdout.decode(), 'exit_code': process.returncode},
                         key='proxy-service-' + operation)['event']
    event['session_id'] = chain.session
    event['tool_input']['command'] = shlex.join(argv)
    return event, answer


@pytest.mark.parametrize('operation', ['expanded', 'full'])
def test_actual_public_entry_saved_read_keeps_original_run(project, tmp_path, operation):
    chain, scene, prefix, full, original, last, before = configured_entry(project, tmp_path)
    event, answer = service_event(chain, scene, prefix, operation)
    expected = cases()['bash-success']['expanded'] if operation == 'expanded' else full['full_path']
    assert answer['run_id'] == full['run_id']
    assert (answer['text'] if operation == 'expanded' else answer['full_path']) == expected
    response = chain.invoke(event, event_name='PostToolUse')
    assert response['hookSpecificOutput']['additionalContext'] == expected
    assert response.get('continue') is not False
    assert last.read_bytes() == before
    assert {str(path): path.read_bytes() for path in Path(full['full_path']).parent.iterdir()
            if path.is_file()} == original
    assert len(list(last.parent.joinpath(scene.policy['session']['runs']).iterdir())) == 1


@pytest.mark.parametrize('change', ['entry', 'cwd', 'policy', 'owned-value'])
def test_declared_entry_does_not_exempt_changed_admission_or_result(project, tmp_path, change):
    chain, scene, prefix, full, original, last, before = configured_entry(project, tmp_path)
    event, answer = service_event(chain, scene, prefix, 'full')
    if change == 'entry':
        event['tool_input']['command'] = event['tool_input']['command'].replace('public-entry.sh', 'other-entry.sh')
    elif change == 'cwd':
        other_cwd = scene.checkout / 'different-cwd'
        other_cwd.mkdir()
        event['cwd'] = str(other_cwd)
    elif change == 'policy':
        event['tool_input']['command'] = event['tool_input']['command'].replace(str(scene.policy_path), str(tmp_path / 'other-policy.json'))
    else:
        answer['full_path'] = str(tmp_path / 'foreign-result')
        event['tool_response']['output'] = json.dumps(answer)
    response = chain.invoke(event, event_name='PostToolUse')
    assert response.get('continue') is not False
    error = json.loads(response['hookSpecificOutput']['additionalContext'])
    assert error['status'] == 'rejected' and error['error']['code'] == 'source_invalid'
    assert last.read_bytes() == before
    assert {str(path): path.read_bytes() for path in Path(full['full_path']).parent.iterdir()
            if path.is_file()} == original


@pytest.mark.parametrize('entry', [
    {'argv': 'tool-result', 'cwd': '/tmp'},
    {'argv': [], 'cwd': '/tmp'},
    {'argv': ['bash', 'tool-result']},
    {'argv': ['bash', 'tool-result'], 'cwd': 'relative'},
])
def test_public_policy_rejects_malformed_service_entry(project, tmp_path, entry):
    chain = Chain(project, tmp_path, 'PUBLIC_ENTRY_POLICY')
    scene = scene_for_chain(chain, project, tmp_path)
    policy = deepcopy(scene.policy)
    policy['service_entry'] = entry
    with pytest.raises(ToolResultFailure) as caught:
        ToolResultPolicy.parse(policy)
    assert caught.value.code == 'invalid_policy'
