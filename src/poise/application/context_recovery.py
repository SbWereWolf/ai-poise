"""Rebuild current workflow metadata through its owners; persist only reader receipts."""
from ..modules.source_reader.domain import exact, text, parse_read
from ..modules.skills.selection import strings
from ..modules.foundation.errors import PoiseError


class ContextRecovery:
    def __init__(self, workflow, reader):
        self.workflow, self.reader = workflow, reader

    def restore(self, request):
        exact(request, {'reason', 'event_id', 'facts', 'cwd', 'reads'}, 'context recovery')
        if request['reason'] not in ('startup', 'resume', 'compact', 'clear', 'manual'):
            raise PoiseError('Unsupported context reset reason')
        text(request['event_id'], 'event_id'); text(request['cwd'], 'cwd')
        strings(request['facts'], 'context facts')
        if not isinstance(request['reads'], list):
            raise PoiseError('reads must be an explicit list')
        if self.reader is None:
            raise PoiseError('source_reader is not configured')
        if request['reads']:
            # Validate the declarative batch before changing generation.
            parse_read({'cwd': request['cwd'], 'generation': 1,
                        'acknowledged': [], 'reads': request['reads']}, self.reader.limits)
        reader = self.reader.reset(request['reason'], request['event_id'])
        metadata = self.workflow.context_metadata(request['facts'])
        result = None
        if request['reads']:
            result = self.reader.read({'cwd': request['cwd'], 'generation': reader['generation'],
                                      'acknowledged': [], 'reads': request['reads']})
        # Concurrent progression invalidates this projection, not the Task or prior evidence.
        task = self.workflow.current_task()
        identity = None if task is None else (task['id'], task['version'])
        expected = None if metadata['task'] is None else (metadata['task']['id'], metadata['task']['version'])
        if identity != expected:
            raise PoiseError('Task context changed while restoring; retry current owner state')
        if self.reader.context() != reader:
            raise PoiseError('Reader generation changed during context recovery; retry')
        return {'schema': 'poise-context-recovery-1', 'status': 'context_restored',
                **metadata, 'reader': reader, 'read_result': result, 'remembered_text': False}
