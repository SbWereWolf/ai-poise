"""One declarative operation, one domain candidate, one publication receipt."""
from __future__ import annotations
from ..modules.goal_config.domain import GoalTypeDefinition, BatchValidationError, fingerprint, problem, require_shape
from ..modules.foundation.errors import DomainError, VersionConflict
from ..modules.goal_config.ports import GoalConfigRepository, ProcessTemplates


def validate_identifier(value, field):
    if not isinstance(value, str) or not value.strip() or value in (".", "..") or any(c in value for c in "/\\\x00"):
        raise DomainError(f"{field}: нужен один безопасный непустой идентификатор")


def validate_revision(value, field):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise DomainError(f"{field}: нужна exact revision")


def validate_batch_request(request: dict, max_changes: int) -> None:
    require_shape(request, {"schema", "request_id", "mode", "goal_type", "expected_revision", "template", "changes"}, "batch")
    if request["schema"] != "goal-config-batch-1":
        raise DomainError("Неподдерживаемая schema пакета; миграции не выполняются")
    for key in ("request_id", "goal_type"):
        validate_identifier(request[key], key)
    mode=request["mode"]
    if mode not in ("create", "update"):
        raise DomainError("mode: create/update")
    changes=request["changes"]
    if not isinstance(changes, list) or len(changes) > max_changes:
        raise DomainError("changes: превышен лимит или список не задан")
    if mode == "create":
        if request["expected_revision"] is not None or not isinstance(request["template"], dict):
            raise DomainError("create требует явный template и expected_revision=null")
        require_shape(request["template"], {"id", "version", "digest"}, "template selection")
    else:
        rev=request["expected_revision"]
        if request["template"] is not None or not isinstance(rev, str) or len(rev)!=64 or any(c not in "0123456789abcdef" for c in rev):
            raise DomainError("update требует exact revision и template=null")


def validate_status_request(request: dict) -> None:
    require_shape(request, {"schema", "goal_type"}, "status")
    if request["schema"] != "goal-config-status-1":
        raise DomainError("Неподдерживаемая schema status")
    validate_identifier(request["goal_type"], "goal_type")


def validate_reconcile_request(request: dict) -> None:
    require_shape(
        request,
        {
            "schema", "request_id", "goal_type", "expected_managed_revision",
            "expected_live_revision", "reason", "authorization",
        },
        "reconcile",
    )
    if request["schema"] != "goal-config-reconcile-1":
        raise DomainError("Неподдерживаемая schema reconcile")
    for key in ("request_id", "goal_type"):
        validate_identifier(request[key], key)
    for key in ("expected_managed_revision", "expected_live_revision"):
        validate_revision(request[key], key)
    for key in ("reason", "authorization"):
        if not isinstance(request[key], str) or not request[key].strip():
            raise DomainError(f"{key}: нужен непустой текст")


class GoalConfigCommands:
    def __init__(self, repository: GoalConfigRepository, templates: ProcessTemplates, max_changes: int):
        if type(max_changes) is not int or max_changes <= 0:
            raise DomainError("max_changes должен быть задан явно")
        self.repository, self.templates, self.max_changes = repository, templates, max_changes

    def apply_batch(self, request: dict) -> dict:
        validate_batch_request(request,self.max_changes)
        mode=request['mode']; changes=request['changes']
        request_digest=fingerprint(request)
        with self.repository.edit(request["goal_type"]) as edit:
            prior=edit.replay(request["request_id"],request_digest)
            if prior is not None:
                return prior
            current=edit.current()
            if mode == "create":
                if current is not None:
                    raise VersionConflict("Тип уже существует; нужен update с явной revision")
                source=self.templates.resolve(request["template"])
                source_revision=None
            else:
                if current is None:
                    raise VersionConflict("Редактируемый тип не существует")
                source_revision=fingerprint(current)
                if source_revision!=request["expected_revision"]:
                    raise VersionConflict(f"Устаревшая revision; текущая {source_revision}")
                source=current
            candidate=GoalTypeDefinition.build(request["goal_type"],source,changes)
            return edit.publish(request,request_digest,source_revision,candidate.data,request["template"])

    def status(self, request: dict) -> dict:
        validate_status_request(request)
        with self.repository.edit(request["goal_type"]) as edit:
            return edit.status()

    def reconcile(self, request: dict) -> dict:
        validate_reconcile_request(request)
        request_digest = fingerprint(request)
        with self.repository.edit(request["goal_type"]) as edit:
            prior = edit.replay(request["request_id"], request_digest)
            if prior is not None:
                return prior
            return edit.reconcile(request, request_digest)
