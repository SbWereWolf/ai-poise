"""Optional native observation, independent of the foreground tool outcome."""
from dataclasses import asdict
from copy import deepcopy
from pathlib import Path
import hashlib

from ...modules.accounting.metrics.domain import ObservationFacts, identity
from ...modules.accounting.metrics.tools import classification, text
from ...modules.accounting.metrics.task_context import task_observations
from ...modules.foundation.errors import PoiseError
from ..goal_config import strict_json
from .configuration import AgentMetricsPolicy
from .runtime import envelope, metric_dispatcher, metric_tools
from .sources import capture_source, source_selections
from .skill_sources import capture_inventory


def capture_event(transport, binding, packet, options, resources):
    policy = AgentMetricsPolicy.parse(strict_json(resources[options['policy_path']]))
    event = packet['event']
    service = metric_tools(binding['project'], policy, event['cwd'])
    dispatcher = metric_dispatcher(service, policy)
    jobs, coverage_observations = [], []
    clock = asdict(transport.clock.observe())
    native = {'external_session': binding['external_session'], 'internal_session': binding['session_id'],
              'invocation_id': packet['invocation_id'], 'event_receipt': deepcopy(packet['event_receipt'])}
    from ..harness_config import load_harness_config
    _, profile, _ = load_harness_config(transport.settings.project_config)
    task = {'state': 'not_applicable', 'reason': 'No Task adapter is connected'}
    if profile['task_adapter'] is not None:
        from ..task_harness_adapter import TaskHarnessAdapter
        try:
            task = TaskHarnessAdapter.observe_current(transport.settings.project_config, profile, binding['session_id'])
        except (PoiseError, OSError, ValueError) as exc:
            task = {'state': 'unavailable', 'reason': 'Current Task observation is unavailable: ' + str(exc)}
    if event['hook_event_name'] == 'PostToolUse':
        response = event.get('tool_response')
        if not isinstance(response, dict):
            return
        scope = {'project': binding['project'], 'session': binding['external_session']}
        if task['state'] == 'known':
            scope.update(task=task['task_id'], sprint=task['sprint_id'])
        source = {'id': 'hooks', 'kind': 'native-hook', 'parser_version': '1'}
        inputs = event.get('tool_input')
        body = inputs.get('command') if isinstance(inputs, dict) and isinstance(inputs.get('command'), str) else text(inputs)
        output = text(response.get('stdout')) + text(response.get('stderr'))
        call = event.get('tool_use_id')
        context = {'call_id': call, 'session_id': binding['external_session'], 'turn_id': event.get('turn_id'),
                   'tool_name': event.get('tool_name'), 'classification': classification(event.get('tool_name', ''), body),
                   'input_hash': hashlib.sha256(body.encode()).hexdigest(),
                   'output_hash': hashlib.sha256(output.encode()).hexdigest(), 'exit_code': response.get('exit_code')}
        facts = ObservationFacts(scope, source, clock['audit_utc'])
        for name, size in (('input_bytes', len(body.encode())), ('output_bytes', len(output.encode()))):
            facts.add('tools', identity([scope, call]), name, size, 'bytes', kind='observed', state='known', context=context)
        associated = task_observations(task, scope, source, clock['audit_utc'])
        facts.facts.extend(associated['facts'])
        facts.metadata.extend(associated['metadata'])
        coverage = {'source_key': identity([scope, 'native-hook']), 'partial_tail': False,
                    'excluded_prefix_events': 0, 'errors': []}
        transcript = event.get('transcript_path')
        if isinstance(transcript, str) and not Path(transcript).is_file():
            coverage['errors'].append({'kind': 'source_unavailable', 'source': transcript,
                                       'reason': 'The native transcript source is unavailable'})
        document = {'kind': 'normalized-capture', 'observation': {**facts.export(), 'scope': scope,
                     'source': source, 'coverage': coverage}, 'policy_digest': policy.digest, 'native': native}
        jobs.append(envelope(document, binding['external_session'], clock))
    def source_job(descriptor, source):
        document = {'kind': 'source-capture', 'descriptor': descriptor,
                    'scope': {'project': binding['project'], 'session': binding['external_session']},
                    'policy_digest': policy.digest, 'turn_id': event.get('turn_id'),
                    'task_association': task, 'native': native}
        frozen = deepcopy(policy.effective)
        frozen['collection']['sources'] = [deepcopy(source)]
        document['policy'] = frozen
        if task['state'] == 'known':
            document['scope'].update(task=task['task_id'], sprint=task['sprint_id'])
        message = event.get('last_assistant_message')
        if isinstance(message, str):
            document['native_message'] = {'bytes': len(message.encode()),
                                         'sha256': hashlib.sha256(message.encode()).hexdigest()}
        return envelope(document, binding['external_session'], clock)

    def coverage_job(source, coverage):
        scope = {'project': binding['project'], 'session': binding['external_session']}
        if task['state'] == 'known':
            scope.update(task=task['task_id'], sprint=task['sprint_id'])
        coverage_observations.append({'facts': [], 'metadata': [], 'scope': scope,
                                      'source': source, 'coverage': coverage})

    def source_failure(source, path, reason, kind='source_unavailable', selection=None):
        selected = source['paths'] if source['kind'] == 'skill-policy' else str(path)
        coverage = {'source_key': identity([source['id'], selected]), 'partial_tail': False,
                    'excluded_prefix_events': 0, 'complete_for_registered_scope': False,
                    'errors': [{'kind': kind, 'source': str(path), 'reason': str(reason)}]}
        if selection is not None:
            coverage.update(selection_key=selection['selection_key'],
                            selection_generation=selection['generation'])
        coverage_job(source, coverage)

    for source in policy.collection['sources']:
        if source['kind'] == 'skill-policy':
            try:
                jobs.append(source_job(capture_inventory(source, event['cwd'], policy.accounting.data['max_files']), source))
            except (PoiseError, OSError, ValueError) as exc:
                source_failure(source, source['paths'][0], exc)
    for selection in source_selections(policy.collection['sources'], policy.accounting.data['max_files']):
        source = selection['source']
        generation = identity([native['invocation_id'], selection['selection_key'], clock['audit_utc']])
        selection['generation'] = generation
        coverage_job(source, {
            'source_key': selection['selection_key'], 'partial_tail': False, 'excluded_prefix_events': 0,
            'complete_for_registered_scope': not selection['errors'], 'errors': selection['errors'],
            'selection': {'key': selection['selection_key'], 'generation': generation,
                          'address': selection['address'], 'observed_at': clock['audit_utc'],
                          'expected_files': len(selection['files']),
                          'owned_elsewhere': selection['owned_elsewhere']},
        })
        for path in selection['files']:
            try:
                descriptor = capture_source(path, source)
                descriptor.update(selection_key=selection['selection_key'], selection_generation=generation)
                jobs.append(source_job(descriptor, source))
            except (PoiseError, OSError, ValueError) as exc:
                source_failure(source, path, exc, selection=selection)
    if coverage_observations:
        if jobs and jobs[0].data['agent_metrics']['kind'] == 'normalized-capture':
            document = deepcopy(jobs[0].data['agent_metrics'])
            document['coverage_observations'] = coverage_observations
            jobs[0] = envelope(document, binding['external_session'], clock)
        else:
            document = {'kind': 'normalized-capture', 'observation': coverage_observations[0],
                        'coverage_observations': coverage_observations[1:],
                        'policy_digest': policy.digest, 'native': native}
            jobs.insert(0, envelope(document, binding['external_session'], clock))
    if jobs:
        batch = {'kind': 'capture-batch', 'jobs': [{'identity': job.identity, 'data': job.data} for job in jobs]}
        dispatcher.submit(envelope(batch, binding['external_session'], clock))
    # close/flush waits for detached workers and must never be called here.
