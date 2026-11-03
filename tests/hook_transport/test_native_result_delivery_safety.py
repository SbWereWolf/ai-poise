"""Independent native-control oracle from the actual direct delivery incident.

These are isolated public dispatcher checks, not fabricated live host calls.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tests.hook_transport.architecture_support import Chain
from tests.hook_transport.session_output_support import configure_chain, scene_for_chain
from tests.runtime_services.session_tool_results_support import cases


@pytest.mark.parametrize('mode', ['audit', 'enforce'])
@pytest.mark.parametrize('case', ['bash-success', 'bash-failure'])
def test_native_presentation_preserves_terminal_and_recovery_data(project, tmp_path, mode, case):
    chain = Chain(project, tmp_path, 'NATIVE_DELIVERY_SAFETY')
    scene = scene_for_chain(chain, project, tmp_path)
    scene.policy['hook']['mode'] = mode
    scene.save_policy()
    configure_chain(chain, scene, after=[chain.command('after')])
    source = deepcopy(cases()[case]['response'])
    source.update(session_id=173, recovery={'resume': 'existing-session', 'pending': False})
    event = scene.native(source, key='terminal-' + case)['event']
    event['session_id'] = chain.session
    event['transcript_path'] = None
    original = deepcopy(event)
    response = chain.invoke(event, event_name='PostToolUse')
    _, delivery = chain.delivery()
    assert response.get('continue') is not False, 'PRESENTATION_REPLACED_NATIVE_RESULT'
    assert response.get('decision') != 'block'
    assert event == original and delivery['event'] == original
    output = next(row for row in delivery['invocations']
                  if row['configuration']['handler_id'] == 'tool-result')
    assert output['result']['control'] == {'action': 'continue', 'reason': None}
    assert [row['label'] for row in chain.order()] == ['after']
    full = scene.service('full')
    assert json.loads(Path(full['full_path']).read_bytes()) == source
    assert source['exit_code'] == (0 if case == 'bash-success' else 7)
    text = response['hookSpecificOutput']['additionalContext']
    if mode == 'enforce':
        assert 'native shortening unsupported' in text.lower()
        assert 'original result preserved' in text.lower()
    else:
        assert text == cases()[case]['brief']
    assert len(text) <= scene.policy['display']['output_chars']
    assert len(text.encode()) <= scene.policy['display']['output_bytes']


def test_native_observation_preserves_a_following_handlers_authorized_stop(project, tmp_path):
    chain = Chain(project, tmp_path, 'NATIVE_FOREIGN_STOP')
    scene = scene_for_chain(chain, project, tmp_path)
    configure_chain(chain, scene, before=[chain.command('before')],
                    after=[chain.command('after', action='stop'), chain.command('tail')])
    event = scene.native(cases()['bash-success']['response'])['event']
    event['session_id'] = chain.session
    response = chain.invoke(event, event_name='PostToolUse')
    assert response.get('continue') is not False
    assert response['systemMessage'] == 'seed|before|after'
    assert [row['label'] for row in chain.order()] == ['before', 'after']
    _, delivery = chain.delivery()
    output = next(row for row in delivery['invocations']
                  if row['configuration']['handler_id'] == 'tool-result')
    after = next(row for row in delivery['invocations']
                 if row['configuration']['handler_id'] == 'after')
    assert output['result']['control'] == {'action': 'continue', 'reason': None}
    assert after['result']['control'] == {'action': 'stop', 'reason': 'fixture stop'}
