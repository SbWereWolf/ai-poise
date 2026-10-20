"""Pure permissions for permanent Task workspace context."""


class WorkspaceError(ValueError):
    pass


def require_context(workspace, read_only, roots):
    if workspace['read_only'] != read_only or workspace['roots'] != roots:
        raise WorkspaceError('workspace_conflict')


def require_prepare_state(status, owns_pending):
    if status == 'reserved':
        if not owns_pending:
            raise WorkspaceError('workspace_pending')
    elif status != 'ready':
        raise WorkspaceError('workspace_' + status)
