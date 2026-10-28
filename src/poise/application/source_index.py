"""Update-before-query orchestration through the selected workspace port."""
from ..modules.source_index.domain import IndexFailure, IndexHealth, execution_succeeded
from ..modules.source_index.ports import IndexWorkspace


class SourceIndex:
    def __init__(self, workspace: IndexWorkspace):
        self.workspace = workspace

    def execute(self, request):
        workspace = self.workspace
        result = {'schema': 'poise-source-index-result-1', 'operation': request.operation,
                  'status': 'disabled', 'checkout': workspace.checkout, 'database': workspace.database,
                  'reason': None, 'recovery': None, 'observations': []}
        if request.operation == 'query' and request.arguments[0] not in workspace.policy.query_commands:
            raise IndexFailure('rejected', 'arguments: подкоманда не разрешена query_commands',
                               'Выберите явно разрешённую read-only подкоманду.')
        if not workspace.policy.enabled:
            return result

        def run(operation, arguments):
            execution = workspace.run(operation, arguments)
            result['observations'].append({'operation': operation, 'execution': execution})
            if not execution_succeeded(execution):
                raise IndexFailure('failed', f'{operation}: процесс или полный захват не завершён успешно',
                                   workspace.recovery(request.operation))
            return execution

        def healthy():
            execution = run('stats', ('stats', '--format', 'json'))
            return IndexHealth.parse(workspace.health_document(execution), workspace.database,
                                     workspace.database_exists(), workspace.recovery('rebuild'))

        try:
            with workspace.hold():
                head = workspace.head()
                if request.operation != 'rebuild':
                    healthy()
                if request.operation in ('update', 'query', 'rebuild'):
                    operation = 'rebuild' if request.operation == 'rebuild' else 'update'
                    run(operation, (operation, '--format', 'json'))
                    healthy()
                if request.operation == 'query':
                    run('query', request.arguments)
                if workspace.head() != head:
                    raise IndexFailure('inconclusive', 'HEAD: рабочая копия изменилась во время операции',
                                       workspace.recovery(request.operation))
                result['status'] = 'complete' if request.operation == 'query' else 'ready'
        except IndexFailure as exc:
            result.update(status=exc.status, reason=exc.reason, recovery=exc.recovery)
        return result
