"""One validated application route for live project configuration updates."""
from ..modules.projects.ports import ProjectConfigUpdatePort
from ..modules.verification.domain import exact_keys
from ..modules.foundation.errors import DomainError


def validate_request(request, max_changes):
    exact_keys(request, {
        "schema", "request_id", "config_path", "expected_revision", "manifest_edits",
        "process_updates", "state_relocation", "probe_repository", "receipt_path",
    }, "project config update")
    if request["schema"] != "project-config-update-1":
        raise DomainError("Unsupported project config update schema; no migration")
    for key in ("request_id", "config_path", "expected_revision", "receipt_path"):
        if not isinstance(request[key], str) or not request[key].strip():
            raise DomainError(f"Explicit {key} required")
    for key in ("manifest_edits", "process_updates"):
        if not isinstance(request[key], list) or len(request[key]) > max_changes:
            raise DomainError(f"{key}: explicit bounded list required")
    if request["state_relocation"] is not None and not isinstance(request["state_relocation"], dict):
        raise DomainError("state_relocation must be an object or null")
    if type(request["probe_repository"]) is not bool:
        raise DomainError("Explicit probe_repository required")


class ProjectConfigCommands:
    def __init__(self, port: ProjectConfigUpdatePort, max_changes: int):
        self.port = port
        self.max_changes = max_changes

    def apply(self, request):
        validate_request(request, self.max_changes)
        return self.port.apply(request)
