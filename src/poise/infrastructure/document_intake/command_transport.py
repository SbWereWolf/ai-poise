"""Phase-aware native tool composition; never binds or constructs a Task runtime."""
from pathlib import Path

from ...application.command_intake import CommandIntake
from ...application.document_intake import IntakeSessions
from ...common import PoiseError
from ...modules.document_intake.command import CommandFailure, decision, parse_tool_event
from ...modules.document_intake.contracts import ReadFailure
from ...modules.document_intake.domain import identity
from ...modules.session_establishment.domain import CallerIdentity
from ..project_binding import read_project_checkout, ProjectBindingFailure
from ..result_views import SavedResponse
from ..session_establishment import establish_project_session
from .command_configuration import load_command_profile
from .command_source import CommandSource, execution_cwd
from .configuration import ReaderConfiguration
from .native import HookNativeAuthority
from .resources import EpisodeResources
from .template import PreparedTemplate


def compose(service, definition_path, event):
    definition = service.definition(definition_path)
    if event['hook_event_name'] not in {e['event'] for e in definition.data['events']}:
        raise CommandFailure('invalid_event', field='hook_event_name')
    parse_tool_event(event)
    path, profile, policy, digest = load_command_profile(service.settings)
    # The native session checkout is independently admitted even for cd to B.
    native_bound = read_project_checkout(service.settings.project_config, Path(event['cwd']))
    base = execution_cwd(event['tool_input']['command'])
    bound = read_project_checkout(service.settings.project_config, base) if base is not None else native_bound
    caller = CallerIdentity.native(bound.config['project'], event['session_id'])
    binding = service.latest_binding(event['session_id'], definition.data['agent_id'])
    if not binding or binding['definition_path'] != str(Path(definition_path).resolve()):
        raise CommandFailure('authority_mismatch', field='session_id')
    establish_project_session(bound, caller)
    configuration = ReaderConfiguration.load(bound, path)

    def authority():
        admission = configuration.policy['admission']
        if (Path(admission['hook_settings']).resolve() != service.settings.path
                or Path(admission['definition_path']).resolve() != Path(definition_path).resolve()):
            raise CommandFailure('authority_mismatch', field='admission')
        return HookNativeAuthority(service=service, project_id=bound.config['project'],
                                   definition_path=Path(definition_path), authority_identity=dict(admission))

    resources = EpisodeResources(configuration, caller, authority, service.clock)
    sessions = IntakeSessions(configuration, resources, PreparedTemplate(Path(profile['session']['registry_template'])))
    source = CommandSource(bound.checkout, policy, cwd_proved=base is not None)
    intake = CommandIntake(resources, sessions, source, policy, digest, service.clock)
    return intake, event | {'execution_cwd': str(base if base is not None else bound.checkout)}


def _failure(result, exc):
    if isinstance(exc, (CommandFailure, ReadFailure)):
        error = exc.as_dict()
    else:
        error = CommandFailure('admission_unavailable', field='native_context').as_dict()
    result.update(status='denied' if result['phase'] == 'pre' else 'unconfirmed', error=error,
                  decision=decision(error['code'], 'deny' if result['phase'] == 'pre' else 'none'))
    return result


def _present(result, service, definition_path, policy):
    phase = result['phase']
    text = result['decision']['reason_code'] + '; ' + result['decision']['action_hint']
    if result['response_path'] is not None:
        text += '\n' + result['response_path']
    if policy is not None:
        event = 'PreToolUse' if phase == 'pre' else 'PostToolUse'
        limit = next(e['context_limit'] for e in service.definition(definition_path).data['events'] if e['event'] == event)
        text = '\n'.join(text.splitlines()[:min(policy['feedback']['max_lines'], limit)])
        maximum = min(policy['feedback']['max_bytes'], limit)
        text = text.encode('utf-8')[:maximum].decode('utf-8', errors='ignore')
    if phase == 'pre':
        action = 'deny' if result['status'] in ('denied', 'conflict') else 'allow'
        return {'hookSpecificOutput': dict(hookEventName='PreToolUse', permissionDecision=action,
                                          permissionDecisionReason=text)}
    return {'systemMessage': text} if result['status'] in ('unconfirmed', 'conflict', 'unmatched') else {}


def tool_event(service, definition_path, event):
    phase = 'pre' if event.get('hook_event_name') == 'PreToolUse' else 'post'
    result, intake, policy = CommandIntake.result(phase), None, None
    if isinstance(event.get('tool_use_id'), str) and isinstance(event.get('tool_name'), str):
        result['call'] = dict(tool_use_id=event['tool_use_id'], tool_name=event['tool_name'],
                              pre_turn_id=event.get('turn_id'), post_turn_id=None)
    fallback = (service.settings.observations / identity([event.get('session_id'), event.get('tool_use_id'), phase])
                / service.settings.raw['response_file'])
    try:
        intake, normalized = compose(service, definition_path, event)
        policy = intake.policy
        result = intake.execute(normalized)
    except (CommandFailure, ReadFailure, PoiseError, ProjectBindingFailure, OSError, ValueError, TypeError) as exc:
        result = _failure(result, exc)
    path = Path(result['response_path']) if result['response_path'] else fallback
    result['response_path'] = str(path)
    try:
        if intake is not None and intake.resources.target is not None and path.is_relative_to(intake.resources.target):
            intake.resources.save_response(path, result)
        else:
            SavedResponse(path, service.settings.raw['file_mode']).save(result, canonical=True)
    except (OSError, ReadFailure) as exc:
        result = _failure(result, CommandFailure('result_save_failed'))
        result['response_path'] = None
    return _present(result, service, definition_path, policy)
