"""Named composition adapters for existing native capability owners."""
from copy import deepcopy
from pathlib import Path
import os
import time

from ..common import PoiseError
from ..modules.hook_transport.domain import parse_native_event
from ..modules.session_establishment.domain import CallerIdentity
from .goal_config import read_document, strict_json


def append_context(response, event, text):
    result = deepcopy(response)
    hook = result.setdefault('hookSpecificOutput', {'hookEventName': event, 'additionalContext': ''})
    previous = hook.get('additionalContext', '')
    hook['additionalContext'] = previous + ('\n' if previous else '') + text
    return result


class BuiltinHandlers:
    def __init__(self, service, definition, binding, deadline, delivery_path):
        self.service, self.definition, self.binding = service, definition, binding
        self.deadline, self.delivery_path = deadline, Path(delivery_path)

    @staticmethod
    def options(definition, name, *, required=True):
        """Resolve one named capability, never legacy top-level router options."""
        from ..modules.hook_transport.dispatch import HandlerConfiguration
        matches = []
        for paths in [definition.data['routes'].get('SessionStart', [])]:
            for address in paths:
                configuration = HandlerConfiguration.parse(read_document(Path(address)))
                for handler in configuration.raw['handlers']:
                    if (handler['implementation']['kind'] == 'builtin'
                            and handler['implementation']['name'] == name):
                        if handler['options'] not in matches:
                            matches.append(handler['options'])
        if not matches and not required:
            return None
        if len(matches) != 1:
            raise PoiseError(f'Configure exactly one coherent {name} capability before using its operation')
        return deepcopy(matches[0])

    @staticmethod
    def preflight(handler, settings, profile):
        name, options = handler['implementation']['name'], handler['options']
        resources = {}

        def snapshot(path):
            path = Path(path)
            if not path.is_absolute():
                raise PoiseError('Builtin policy requires an explicit absolute path')
            address = str(path)
            if address not in resources:
                resources[address] = path.read_bytes().decode('utf-8')
            return strict_json(resources[address])

        def reader(path):
            from .document_intake.configuration import ReaderConfiguration
            snapshot(path)
            configuration = ReaderConfiguration.load(None, path, snapshots=resources)
            snapshot(configuration.policy['command_intake']['policy'])
            from .document_intake.command_configuration import load_command_policy
            load_command_policy(configuration.policy, snapshots=resources)
        if name == 'lifecycle-consumer':
            from .hook_lifecycle import consumer_options
            consumer = consumer_options(options['consumer'])
            for key in ('CODEX_THREAD_ID', 'CODEX_SESSION_ID', 'POISE_CALLER_BINDING'):
                if key in consumer['environment'] and consumer['environment'][key] != os.environ.get(key):
                    raise PoiseError('Lifecycle configuration cannot replace the genuine caller identity')
        elif name == 'general-context':
            from ..modules.work_context.domain import ContextPolicy
            policy = ContextPolicy.from_configuration(snapshot(options['policy_path']))
            reader(policy.data['reader_policy'])
            from .project_instructions import FileInstructionSource
            from ..modules.skills.instructions import InstructionPolicy
            instructions = policy.data['instructions']
            snapshot(instructions['catalog'])
            snapshot(instructions['policy'])
            source = FileInstructionSource(instructions['catalog'], instructions['root'], snapshots=resources)
            InstructionPolicy.from_configuration(source.policy_document(instructions['policy'])[0],
                                                 source.catalogue_metadata())
            if (profile['task_adapter'] is None and any(policy.data[key]['enabled']
                    for key in ('work_owner', 'check_owner'))):
                raise PoiseError('task_adapter_required: connect Task before enabling its context owners')
        elif name == 'command-intake':
            from .document_intake.command_configuration import load_command_profile
            reader(options['policy_path'])
            load_command_profile(settings, policy_path=options['policy_path'], snapshots=resources)
        elif name == 'check-invocation':
            from ..modules.project_checks.mcp import CheckTransportPolicy
            CheckTransportPolicy.parse(snapshot(options['policy_path']))
            snapshot(options['checks_policy_path'])
        elif name == 'agent-metrics':
            from .agent_metrics.configuration import AgentMetricsPolicy
            AgentMetricsPolicy.parse(snapshot(options['policy_path']))
        elif name == 'tool-result':
            from ..modules.result_views.tool_results import ToolResultPolicy
            policy = ToolResultPolicy.parse(snapshot(options['policy_path'])).data
            if 'session' not in policy:
                raise PoiseError('tool-result hook requires explicit session and parser policies')
            snapshot(policy['placement']['policy'])
        elif name == 'capability-summary':
            settings.require_summary(options['summary'])
            if len(options['probes']) > settings.raw['max_probes']:
                raise PoiseError('Too many configured capability probes')
        return resources

    def invoke(self, handler, packet, *, resources):
        name = handler['implementation']['name']
        options, event, response = handler['options'], packet['event'], deepcopy(packet['response'])
        execution = None
        if name == 'agent-metrics':
            from .agent_metrics.native import capture_event
            capture_event(self.service, self.binding, packet, options, resources)
        elif name == 'capability-summary':
            readiness = self.service._readiness(self.binding, summary_options=options['summary'])
            summary = readiness.refresh(self.binding['session_id'], event['cwd'],
                                        options['probes'], self.service.clock).to_dict()
            templates = options['summary']
            lines = [templates['header_template'].format(ready=summary['ready'], checked_at=summary['checked_at'],
                      workspace=summary['workspace'], snapshot_path=str(readiness.store.path))]
            lines.extend(templates['item_template'].format(**item) for item in summary['observations'])
            response = append_context(response, event['hook_event_name'], '\n'.join(lines))
        elif name == 'command-intake':
            from .document_intake.command_transport import tool_event
            output = (tool_event(self.service, self.binding['definition_path'], event,
                                policy_path=options['policy_path'], snapshots=resources)
                      if event.get('tool_name') == 'Bash' else {})
            response.update(output)
        elif name == 'check-invocation':
            from .project_checks.hook_transport import tool_event
            response = tool_event(self.service, self.binding, self.delivery_path, packet,
                                  options, resources)
        elif name == 'tool-result':
            from ..composition import tool_result_tools
            from ..modules.result_views.tool_results import ToolResultPolicy, ToolResultFailure, encoded
            from .tool_results import service_delivery
            policy = ToolResultPolicy.parse(strict_json(resources[options['policy_path']])).data
            caller = CallerIdentity.harness('native', self.binding['external_session'])
            owner = tool_result_tools(None, policy, {}, caller=caller, resources=resources)
            try:
                text = service_delivery(event, owner, options['policy_path'], self.service.settings)
                if text is None:
                    native = parse_native_event(event, self.definition)
                    result = owner.execute({'schema': 'poise-tool-result-request-1', 'operation': 'hook',
                        'request_id': event['tool_use_id'], 'event': {**event, 'turn_id': native['turn_id']},
                        'streams': {'stdout': None, 'stderr': None},
                        'capture': {'state': 'unknown', 'reason': 'native source not yet classified'},
                        'exit_code': None}, 'agent')
                    if result['status'] == 'pending':
                        return {'schema': 'poise-hook-handler-result-1', 'invocation_id': packet['invocation_id'],
                                'response': response, 'control': {'action': 'continue', 'reason': None}}, execution
                    text = result['preview']
            except ToolResultFailure as error:
                native = event.get('tool_response')
                code = native.get('exit_code') if isinstance(native, dict) else None
                code = code if type(code) is int else None
                response = append_context(response, event['hook_event_name'], encoded(error.result(code)))
                response['continue'] = False
                return {'schema': 'poise-hook-handler-result-1', 'invocation_id': packet['invocation_id'],
                        'response': response, 'control': {'action': 'stop', 'reason': str(error)}}, execution
            response = append_context(response, event['hook_event_name'], text)
            response['continue'] = False
        elif name == 'lifecycle-consumer':
            from .hook_lifecycle import consumer_options, prepare, deliver
            consumer = consumer_options(options['consumer'])
            # This invocation retains the real host identity. Configuration may
            # add process options, but cannot manufacture another native caller.
            consumer['environment'] = {**os.environ, **consumer['environment']}
            path, record = prepare(self.service.settings, self.binding['definition_path'], event,
                                   packet['event_receipt'], self.deadline)
            try:
                output = deliver(self.service.settings, path, record, options=consumer)
            except (PoiseError, OSError, ValueError) as exc:
                from .hook_dispatch import HandlerExecutionFailure
                raise HandlerExecutionFailure(
                    f'Lifecycle consumer did not complete ({record.get("reason")}); '
                    f'full consumer result: {path}; admitted event delivery: {self.delivery_path}',
                    record['execution']) from exc
            execution = record['execution']
            if 'hookSpecificOutput' in output:
                response = append_context(response, event['hook_event_name'],
                                          output['hookSpecificOutput']['additionalContext'])
            if 'systemMessage' in output:
                response['systemMessage'] = output['systemMessage']
        elif name == 'general-context':
            response = self.general_context(options, packet, response, resources)
        elif name == 'task-context':
            response = self.task_context(options, packet, response)
        elif name == 'task-runtime-cleanup':
            # Transport release closes the committed End exactly once after
            # selected consumers. This handler selects the existing Task cleanup
            # capability; it does not synthesize or release another native event.
            from .session_establishment import cleanup_project_session_runtime
            cleanup_project_session_runtime(self.service.settings.project_config, self.binding['session_id'])
        else:
            raise PoiseError('Unknown builtin handler')
        return {'schema': 'poise-hook-handler-result-1', 'invocation_id': packet['invocation_id'],
                'response': response, 'control': {'action': 'continue', 'reason': None}}, execution

    def general_context(self, options, packet, response, resources):
        from ..modules.work_context.domain import ContextPolicy, CARD_PREFIX, encode
        from .work_context import native_context_event
        from .hook_context import NativeContextPresentation
        kind, event = packet['event']['hook_event_name'], packet['event']
        if kind == 'SessionEnd':
            return response  # The separately selected lifecycle consumer owns recovery.
        policy = ContextPolicy.from_configuration(strict_json(resources[options['policy_path']]))
        owner, context_event = native_context_event(self.service, self.binding['definition_path'],
            self.binding, event, packet['event_receipt'], self.deadline, policy=policy, snapshots=resources)
        if kind == 'PreCompact':
            outcome = owner.capture(context_event)
        elif kind == 'PostCompact':
            outcome = owner.compact()
        else:
            native = parse_native_event(event, self.definition)
            presentation = NativeContextPresentation(self.service, response, self.definition,
                                                     native, self.binding, self.deadline)
            outcome = owner.restore(context_event, presentation.available_bytes())
            response = presentation.complete(CARD_PREFIX+encode(outcome.card))
        if not outcome.complete:
            raise PoiseError(f'{outcome.reason}: retry the saved admitted event delivery {self.delivery_path}')
        return response

    def task_context(self, options, packet, response):
        from .session_establishment import establish_poise
        service, binding, event, receipt = self.service, self.binding, packet['event'], packet['event_receipt']
        caller = CallerIdentity.native(binding['project'], binding['external_session'])
        runtime = establish_poise(service.settings.project_config, caller, [], service.clock,
                                  binding['session_id']).runtime
        task = runtime.current_task()
        active = task if task is not None and task['status'] not in ('completed', 'cancelled') else None
        kind = event['hook_event_name']
        if kind == 'UserPromptSubmit':
            message = {'conversation_id': event['session_id'], 'message_id': event['turn_id'],
                       'occurred_at': None, 'reason': None, 'subject': None}
            runtime.interactions.record(runtime.interactions.prepare([message]), runtime.session, active)
            service.registry.remember_message(binding['session_id'], message)
        recovery = None
        if kind == 'SessionStart' and runtime.context_reader is not None:
            recovery = runtime.restore_context(reason=event['source'],
                event_id=f"hook-event:{receipt['event_id']}", facts=[], cwd=event['cwd'], reads=[])
        state = ('idle' if task is None else
                 f"{task['id']} / {task['status']} / {task['workflow']['stages'][task['stage_index']]['id']}")
        if kind in ('SessionStart', 'UserPromptSubmit'):
            text = options['context_template'].format(launcher=binding['launcher'], state=state)
            if recovery is not None:
                text += ('\nContext generation: ' + str(recovery['reader']['generation'])
                         + '; event: ' + recovery['reader']['event_id']
                         + '; reason: ' + recovery['reader']['reason']
                         + '. Use restore_context with that identity and explicit reads. '
                         + 'Prior receipts do not prove remembered text.')
            response = append_context(response, kind, text)
        elif kind == 'Stop':
            response['systemMessage'] = options['stop_template'].format(state=state)
        return response
