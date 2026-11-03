"""Isolated configuration clients; no output implementation or fake authority."""
from copy import deepcopy

from poise.common import PoiseError
from tests.hook_transport.architecture_support import literal
from tests.runtime_services.session_tool_results_support import OutputCapabilityMissing, SessionScenario


def scene_for_chain(chain, project, parent):
    """Reuse the already publicly selected general profile; never republish it."""
    selected = {**project, 'cfg': chain.general.profile, 'config_path': chain.general.path}
    scene = SessionScenario(selected, parent)
    # Native observation preserves the host result; legacy enforcement is tested explicitly.
    scene.policy['hook']['mode'] = 'audit'
    scene.save_policy()
    return scene


def output_handler(policy_path):
    handler = literal('handler-configuration.json')['handlers'][0]
    handler.update(id='tool-result', implementation={'kind': 'builtin', 'name': 'tool-result'},
                   options={'policy_path': str(policy_path)})
    return handler


def install_output(service, definition, request_id):
    try:
        value = service.install({'request_id': request_id,
                                 'expected_revision': service.revision(),
                                 'definition': deepcopy(definition)})
    except PoiseError as error:
        if str(error) == 'Unregistered builtin handler; select a registered implementation':
            raise OutputCapabilityMissing('F08_OUTPUT_BUILTIN_MISSING') from error
        if str(error) == 'Command intake requires the explicit Bash matcher':
            raise OutputCapabilityMissing('F08_GENERAL_OUTPUT_MATCHER_MISSING') from error
        raise
    assert value['status'] == 'installed'
    return value


def configure_chain(chain, scene, *, matcher='Bash', before=(), after=(), include_end=False):
    # Real healthy configuration control, before the new capability is selected.
    chain.install([])
    descriptors = literal('native-events.json')
    definition = deepcopy(chain.definition)
    events = ('SessionStart', 'PreToolUse', 'PostToolUse', 'SessionEnd') if include_end else (
        'SessionStart', 'PreToolUse', 'PostToolUse')
    definition['events'] = [row['descriptor'] for row in descriptors
                            if row['descriptor']['event'] in events]
    for event in definition['events']:
        if event['event'] in ('PreToolUse', 'PostToolUse'):
            event['matcher'] = matcher
    path = chain.configuration('session-output',
                               [*before, output_handler(scene.policy_path), *after])
    definition['routes'] = {item['event']: [] for item in definition['events']}
    definition['routes']['PostToolUse'] = [path]
    chain.installation = install_output(chain.service, definition,
                                       'f08-isolated-output-' + scene.parent.name)
    chain.definition = definition
    chain.invoke()
    # Only this test-owned native session, after its actual public Start.
    scene.env.pop('POISE_CALLER_BINDING')
    scene.env['CODEX_THREAD_ID'] = chain.session
    return definition
