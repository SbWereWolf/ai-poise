"""Selected input composes the existing explicit verification operation."""
from copy import deepcopy

from ..modules.foundation.errors import PoiseError
from ..modules.work.selection import (
    SelectedResultIntent, SelectionRefusal, selection_result, task_read_packet,
)


class SelectedResultSubmission:
    def __init__(self, work):
        self.work = work
        self.runtime = work.runtime
        self.resources = work.resources

    def submit(self, args):
        intent = SelectedResultIntent.parse(args['selection'])
        try:
            return self._submit(args, intent)
        except PoiseError as exc:
            if not isinstance(exc, SelectionRefusal):
                exc = SelectionRefusal(None, str(exc), kind='continue_work', packet=task_read_packet())
            exc.selection = {'schema': intent.value['schema'], 'request_id': intent.value['request_id']}
            raise exc

    def _submit(self, args, intent):
        h = self.runtime
        data = h.current_task()
        if data is None or data['claimed_by'] != h.session:
            raise SelectionRefusal('selection_ownership_required', 'Acquire the current Task before submitting its selection.',
                                   kind='refresh_context', packet=task_read_packet())
        context = h.result_selection_context(data)
        expected = intent.value['expected']
        identity = h.packet_digest(args)
        saved = self.resources.selection_request(data, intent.value['request_id'])
        if saved is not None and saved['digest'] != identity:
            raise SelectionRefusal('selection_request_conflict', 'The admitted request_id belongs to different input; preserve the original request or use a new identity after reading context.')
        for key in ('task', 'stage', 'iteration'):
            if context[key] != expected[key]:
                raise SelectionRefusal('selection_stale', f'Expected {expected}; current task/stage/iteration is '
                                       f'{context["task"]}/{context["stage"]}/{context["iteration"]}. Read current context.',
                                       kind='refresh_context', packet=task_read_packet())
        if saved is not None:
            if saved['actor'] != h.session:
                raise SelectionRefusal('selection_ownership_required', 'The original intent belongs to another actor; read the current stage contract.',
                                       kind='refresh_context', packet=task_read_packet())
            if data['status'] == 'verified':
                result = h.verify(None, packet_digest=identity)
                return selection_result(result, intent, context['progression'])
            if data['pending'] is not None:
                raise SelectionRefusal('selection_recovery_pending',
                                       f'Original attempt is pending: {data["pending"]}. Its check owner must establish genuine termination/receipts before retry; do not launch another attempt.',
                                       kind='continue_work', packet=task_read_packet())
            if saved.get('guard') is not None and data['publication'] is not None:
                result = h.verify(deepcopy(saved['payload']), packet_digest=identity, selection_guard=saved)
                return selection_result(result, intent, context['progression'])
            payload, artifacts = deepcopy(saved['payload']), deepcopy(saved['artifacts'])
            self.resources.validate_selection_sources(saved)
        else:
            if data['status'] != 'active' or context['version'] != expected['version']:
                raise SelectionRefusal('selection_stale', f'Expected {expected}; current version/status is {context["version"]}/{data["status"]}. Read current context.',
                                       kind='refresh_context', packet=task_read_packet())
            payload = intent.compose_result(context['result_template'], context['required_sections'])
            artifacts, sources = self.resources.prepare_selection_sources(data, intent.value['files'])
            factory = self.resources.factory()
            prepared = self.resources.prepare_selection(factory, artifacts, {'sources': sources})
            original_paths = deepcopy(payload['artifact_paths'])
            if not isinstance(original_paths, list) or any(not isinstance(p, str) or not p for p in original_paths):
                raise SelectionRefusal('selection_contract_invalid', 'metadata.artifact_paths must be an explicit list of existing artifact paths')
            payload['artifact_paths'] = list(dict.fromkeys(original_paths + [str(item.path) for item in prepared]))
            if not h.validate_stage_entry(data)['passed']:
                raise SelectionRefusal('selection_stale', 'Current stage entry is no longer satisfied; inspect the current Task.',
                                       kind='refresh_context', packet=task_read_packet())
            h.validate_stage_result(data['id'], payload)
            h.validate_stage_scope(data)
            h.validate_verification_artifacts(original_paths, data)
            saved = self.resources.admit_selection(data, {
                'request_id': intent.value['request_id'], 'digest': identity, 'actor': h.session,
                'expected': deepcopy(expected), 'input': deepcopy(args), 'payload': deepcopy(payload),
                'artifacts': deepcopy(artifacts), 'sources': sources, 'original_paths': original_paths,
            })
        payload['artifact_paths'] = deepcopy(saved['original_paths'])
        result = self.work._verify_explicit({'result': payload, 'artifacts': artifacts},
                                            packet_digest=identity, selection_guard=saved)
        return selection_result(result, intent, context['progression'])
