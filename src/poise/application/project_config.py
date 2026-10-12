"""One validated application route for live project configuration updates."""
from ..modules.projects.ports import ProjectConfigUpdatePort
from ..modules.verification.domain import exact_keys
from ..modules.foundation.errors import DomainError
from ..modules.foundation.validation import nonempty


def validate_relocation(move):
    exact_keys(move, {'schema', 'expected_source', 'destination', 'source_disposition',
                      'hook_destinations'}, 'state_relocation')
    if move['schema'] != 'project-state-relocation-1':
        raise DomainError('Unsupported state_relocation.schema')
    for key in ('expected_source', 'destination', 'source_disposition'):
        nonempty(move[key], f'state_relocation.{key}')
    if move['source_disposition'] not in ('retain', 'delete_after_publish'):
        raise DomainError('state_relocation.source_disposition is unsupported')
    if not isinstance(move['hook_destinations'], list):
        raise DomainError('state_relocation.hook_destinations: list required')
    for index, item in enumerate(move['hook_destinations']):
        label = f'state_relocation.hook_destinations[{index}]'
        exact_keys(item, {'installation_id', 'settings_path', 'settings'}, label)
        for key in ('installation_id', 'settings_path'):
            nonempty(item[key], f'{label}.{key}')
        if not isinstance(item['settings'], dict):
            raise DomainError(f'{label}.settings: object required')


def validate_request(request, max_changes):
    if isinstance(request, dict) and request.get('schema') == 'project-runtime-resources-bootstrap-1':
        exact_keys(request, {'schema', 'request_id', 'config_path', 'expected_revision',
                             'runtime_resources', 'receipt_path'}, 'runtime resources bootstrap')
        for key in ('request_id', 'config_path', 'expected_revision', 'receipt_path'):
            nonempty(request[key], key)
        from ..modules.projects.resources import validate_resources
        validate_resources(request['runtime_resources'])
        return
    workspace = isinstance(request, dict) and request.get('schema') == 'project-worktree-root-1'
    exact_keys(request, {
        "schema", "request_id", "config_path", "expected_revision", "manifest_edits",
        "process_updates", "state_relocation", "probe_repository", "receipt_path",
    } | ({'reason', 'authorization'} if workspace else set()), "project config update")
    if request["schema"] not in ("project-config-update-1", "project-worktree-root-1"):
        raise DomainError("Unsupported project config update schema; no migration")
    for key in ("request_id", "config_path", "expected_revision", "receipt_path"):
        nonempty(request[key], key)
    for key in ("manifest_edits", "process_updates"):
        if not isinstance(request[key], list) or len(request[key]) > max_changes:
            raise DomainError(f"{key}: explicit bounded list required")
    if request["state_relocation"] is not None and not isinstance(request["state_relocation"], dict):
        raise DomainError("state_relocation must be an object or null")
    if request['state_relocation'] is not None:
        validate_relocation(request['state_relocation'])
    if type(request["probe_repository"]) is not bool:
        raise DomainError("Explicit probe_repository required")
    if workspace:
        if any(not isinstance(request[key], str) or not request[key].strip()
               for key in ('reason', 'authorization')):
            raise DomainError('Workspace-root correction requires reason and authorization')
        edits = request['manifest_edits']
        if (len(edits) != 1 or not isinstance(edits[0], dict)
                or edits[0].get('path') != ['paths', 'worktrees']
                or request['process_updates'] or request['state_relocation'] is not None):
            raise DomainError('Workspace-root correction changes only paths.worktrees')


class ProjectConfigCommands:
    def __init__(self, port: ProjectConfigUpdatePort, max_changes: int):
        self.port = port
        self.max_changes = max_changes

    def apply(self, request):
        validate_request(request, self.max_changes)
        if request['schema'] == 'project-runtime-resources-bootstrap-1':
            return self.port.bootstrap_runtime_resources(request)
        return self.port.apply(request)
