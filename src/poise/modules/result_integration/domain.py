from __future__ import annotations

from dataclasses import dataclass, replace
import re

from ..foundation.errors import DomainError


_COMMIT = re.compile(r"[0-9a-f]{40,64}")
_SCHEMA = "existing-task-worktree-1"
_LEGACY_FIELDS = {
    "kind", "intent", "status", "version", "target_before", "target_after",
    "conflicts", "resolutions", "merge", "failure", "cleanup", "history",
}


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise DomainError(f"{name} must be a nonempty string")
    return value


def _commit(value, name):
    if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
        raise DomainError(f"{name} must be a full lowercase commit ID")
    return value


def _resolution(value):
    if not isinstance(value, dict) or set(value) != {"path", "resolution"}:
        raise DomainError("Resolution requires exact path and resolution fields")
    path = _text(value["path"], "resolution path")
    if path.startswith("/") or ".." in path.split("/"):
        raise DomainError("Resolution path must stay inside the task worktree")
    return {"path": path, "resolution": _text(value["resolution"], "resolution rationale")}


@dataclass(frozen=True)
class IntegrationIntent:
    request_id: str
    task_id: str
    expected_source_commit: str
    expected_target_commit: str
    authorization: str
    commit_message: str
    resolutions: tuple[dict, ...]
    config_backup_paths: tuple[str, ...]

    @classmethod
    def parse(cls, value):
        keys = {"request_id", "task_id", "expected_source_commit", "expected_target_commit",
                "authorization", "commit_message", "resolutions"}
        if not isinstance(value, dict) or set(value) not in (keys, keys | {"config_backup_paths"}):
            raise DomainError("integrate input requires exact intent and resolutions fields")
        resolutions = value["resolutions"]
        if not isinstance(resolutions, list):
            raise DomainError("resolutions must be an explicit list")
        parsed = tuple(_resolution(item) for item in resolutions)
        if len({item["path"] for item in parsed}) != len(parsed):
            raise DomainError("Resolution paths must be unique")
        backup_paths = value.get("config_backup_paths", [])
        if not isinstance(backup_paths, list):
            raise DomainError("config_backup_paths must be a list")
        if any(
            not isinstance(path, str) or not path or "\x00" in path
            or "\\" in path or path.startswith("/")
            or any(part in ("", ".", "..") for part in path.split("/"))
            for path in backup_paths
        ) or len(set(backup_paths)) != len(backup_paths):
            raise DomainError("config_backup_paths must contain unique safe relative paths")
        return cls(
            _text(value["request_id"], "request_id"),
            _text(value["task_id"], "task_id"),
            _commit(value["expected_source_commit"], "expected_source_commit"),
            _commit(value["expected_target_commit"], "expected_target_commit"),
            _text(value["authorization"], "authorization"),
            _text(value["commit_message"], "commit_message"),
            parsed,
            tuple(backup_paths),
        )

    def identity(self):
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "expected_source_commit": self.expected_source_commit,
            "expected_target_commit": self.expected_target_commit,
            "authorization": self.authorization,
            "commit_message": self.commit_message,
            "config_backup_paths": list(self.config_backup_paths),
        }


@dataclass(frozen=True)
class IntegrationRun:
    intent: IntegrationIntent
    status: str
    phase: str
    version: int
    accepted_commit: str
    task_branch: str
    task_worktree: str
    temporary_backup_directory: str
    last_included_target: str | None
    integration_head: str | None
    target_after: str | None
    conflicts: tuple[str, ...]
    resolutions: tuple[dict, ...]
    checks: tuple[dict, ...]
    publication: dict | None
    temporary_backups: tuple[str, ...]
    failure: dict | None
    cleanup: dict
    history: tuple[dict, ...]
    drift_retries: int

    @property
    def complete(self) -> bool:
        return (
            self.status == "integrated"
            and self.phase == "integrated"
            and self.cleanup == {
                "task_worktree": "removed",
                "task_branch": "deleted",
                "temporary_backups": "removed",
            }
        )

    @classmethod
    def new(cls, intent, task_branch, task_worktree, temporary_backup_directory):
        cleanup = {
            "task_worktree": "pending",
            "task_branch": "pending",
            "temporary_backups": "pending",
        }
        return cls(
            intent=intent, status="prepared", phase="prepared", version=0,
            accepted_commit=intent.expected_source_commit,
            task_branch=_text(task_branch, "task branch"),
            task_worktree=_text(task_worktree, "task worktree"),
            temporary_backup_directory=_text(
                temporary_backup_directory, "temporary backup directory"
            ),
            last_included_target=None, integration_head=intent.expected_source_commit,
            target_after=None, conflicts=(), resolutions=(), checks=(), publication=None,
            temporary_backups=(), failure=None, cleanup=cleanup,
            history=({"status": "prepared", "phase": "prepared"},), drift_retries=0,
        )

    @property
    def requires_destination_admission(self):
        """Published recovery and terminal replay do not prepare a new candidate."""
        if self.phase == "integrated" and self.status == "integrated":
            return False
        if self.phase == "cleanup_pending" and self.status == "cleanup_pending" \
                and self.publication is not None:
            return False
        if self.phase in {
            "prepared", "updating", "candidate_ready", "checks_failed",
            "candidate_failed", "awaiting_resolution", "publishing", "publication_failed",
        }:
            return True
        raise DomainError("Saved result integration phase is invalid")

    @classmethod
    def restore(cls, value):
        if (not isinstance(value, dict) or value.get("kind") != "result_integration"
                or value.get("schema") != _SCHEMA):
            raise DomainError("Saved result integration state is invalid")
        intent = IntegrationIntent.parse({**value["intent"], "resolutions": []})
        return cls(
            intent=intent, status=value["status"],
            phase=_text(value["phase"], "Saved result integration phase"),
            version=value["version"], accepted_commit=value["accepted_commit"],
            task_branch=value["task_branch"], task_worktree=value["task_worktree"],
            temporary_backup_directory=value["temporary_backup_directory"],
            last_included_target=value["last_included_target"],
            integration_head=value["integration_head"], target_after=value["target_after"],
            conflicts=tuple(value["conflicts"]), resolutions=tuple(value["resolutions"]),
            checks=tuple(value["checks"]), publication=value["publication"],
            temporary_backups=tuple(value["temporary_backups"]), failure=value["failure"],
            cleanup=dict(value["cleanup"]), history=tuple(value["history"]),
            drift_retries=value["drift_retries"],
        )

    @classmethod
    def recover_legacy(cls, value, task_branch, task_worktree,
                       temporary_backup_directory):
        if (not isinstance(value, dict) or set(value) != _LEGACY_FIELDS
                or value.get("kind") != "result_integration"
                or value.get("status") != "blocked"):
            raise DomainError("Saved result integration state is invalid")
        legacy_intent = value.get("intent")
        if (not isinstance(legacy_intent, dict)
                or legacy_intent.get("task_id") != "0048"
                or legacy_intent.get("request_id") != "integrate-0048-1"):
            raise DomainError("Legacy integration state is not eligible for automatic recovery")
        failure = value.get("failure")
        if (not isinstance(failure, dict)
                or failure.get("reason") != "merge_failed_without_conflicts"
                or value.get("target_after") is not None
                or value.get("conflicts") != [] or value.get("resolutions") != []
                or value.get("cleanup") != {"worktree": "pending", "branch": "pending"}):
            raise DomainError("Legacy integration state is not eligible for automatic recovery")
        intent = IntegrationIntent.parse({**legacy_intent, "resolutions": []})
        history = value.get("history")
        if not isinstance(history, list):
            raise DomainError("Legacy integration history is invalid")
        recovered = cls.new(intent, task_branch, task_worktree, temporary_backup_directory)
        return replace(
            recovered, version=value["version"] + 1,
            history=tuple(history) + ({
                "status": "prepared", "phase": "prepared",
                "event": "legacy_recovery_started",
                "details": {"preserved_failure": failure},
            },),
        )

    def _step(self, status, phase, event=None, details=None, **changes):
        entry = {"status": status, "phase": phase}
        if event is not None:
            entry["event"] = event
        if details is not None:
            entry["details"] = details
        return replace(self, status=status, phase=phase, version=self.version + 1,
                       history=self.history + (entry,), **changes)

    def begin_update(self, target):
        if self.phase not in ("prepared", "publishing"):
            raise DomainError("Integration cannot start a target update from this phase")
        return self._step(
            "running", "updating", "target_update_started",
            last_included_target=_commit(target, "included target"),
            conflicts=(), resolutions=(), publication=None, failure=None,
        )

    def await_resolution(self, conflicts, receipt):
        paths = tuple(sorted(conflicts))
        if self.phase != "updating" or not paths:
            raise DomainError("A running integration requires observed conflicts")
        return self._step(
            "awaiting_resolution", "awaiting_resolution", "conflicts_observed",
            details={"receipt": receipt}, conflicts=paths,
        )

    def continue_with(self, resolutions):
        if self.phase != "awaiting_resolution":
            raise DomainError("No conflict continuation is pending")
        parsed = tuple(_resolution(value) for value in resolutions)
        if {item["path"] for item in parsed} != set(self.conflicts):
            raise DomainError("Provide one resolution for every observed conflict, and no unrelated paths")
        return self._step(
            "running", "updating", "resolutions_declared", resolutions=parsed,
        )

    def candidate_ready(self, integration_head, receipt):
        if self.phase != "updating" or self.last_included_target is None:
            raise DomainError("Only an updated task branch can record its head")
        return self._step(
            "running", "candidate_ready", "candidate_ready", details={"receipt": receipt},
            integration_head=_commit(integration_head, "integration head"), failure=None,
        )

    def candidate_failed(self, reason, receipt):
        if self.phase != "updating":
            raise DomainError("Only a running target update can record a failure")
        failure = {"reason": _text(reason, "candidate failure reason"), "receipt": receipt}
        return self._step(
            "blocked", "candidate_failed", "candidate_failed", details=failure, failure=failure,
        )

    def retry_candidate(self):
        if self.phase != "candidate_failed":
            raise DomainError("Only a failed candidate can be retried")
        return self._step(
            "running", "updating", "candidate_retry_started",
            conflicts=(), resolutions=(), failure=None,
        )

    def checks_recorded(self, receipts):
        if self.phase != "candidate_ready":
            raise DomainError("Checks require a ready integration candidate")
        batch = tuple(receipts)
        checks = self.checks + batch
        if all(item.get("passed") is True for item in batch):
            return self._step(
                "running", "publishing", "checks_passed", checks=checks, failure=None,
                details={"check_ids": [item["id"] for item in batch]},
            )
        failure = {"reason": "checks_failed", "checks": list(batch)}
        return self._step(
            "blocked", "checks_failed", "checks_failed", details=failure,
            checks=checks, failure=failure,
        )

    def retry_checks(self, integration_head=None):
        if self.phase != "checks_failed":
            raise DomainError("Only failed integration checks can be retried")
        return self._step(
            "running", "candidate_ready", "checks_retry_started", failure=None,
            integration_head=_commit(self.integration_head if integration_head is None else integration_head, "integration head"),
            details={"previous_head": self.integration_head,
                     "candidate_head": self.integration_head if integration_head is None else integration_head},
        )

    def drifted(self, receipt):
        if self.phase != "publishing":
            raise DomainError("Target drift is observed only during publication")
        return self._step(
            "running", "publishing", "target_drifted", details={"receipt": receipt},
            drift_retries=self.drift_retries + 1,
        )

    def publication_blocked(self, reason, receipt):
        if self.phase != "publishing":
            raise DomainError("Publication failure requires a tested candidate")
        failure = {"reason": _text(reason, "publication failure reason"), "receipt": receipt}
        return self._step(
            "blocked", "publication_failed", "publication_failed",
            details=failure, failure=failure,
        )

    def retry_publication(self):
        if self.phase != "publication_failed":
            raise DomainError("Only a failed publication can be retried")
        return self._step(
            "running", "publishing", "publication_retry_started", failure=None,
        )

    def recheck_publication(self):
        if self.phase not in ("publishing", "publication_failed"):
            raise DomainError("Only a pending publication can return to candidate checks")
        return self._step(
            "running", "candidate_ready", "publication_checks_restarted", failure=None,
        )

    def require_current_checks(self):
        if self.phase != "publishing":
            raise DomainError("Current verification requires a publication candidate")
        return self._step(
            "running", "candidate_ready", "current_checks_required", failure=None,
        )

    def publication_confirmed(self, target_ref, receipt, *, no_op, recovered):
        if (self.phase != "publishing" or self.integration_head is None
                or self.last_included_target is None):
            raise DomainError("Publication requires a tested candidate")
        publication = {
            "status": "confirmed", "target_ref": _text(target_ref, "target ref"),
            "commit": self.integration_head,
            "last_included_target": self.last_included_target,
            "no_op": bool(no_op), "recovered": bool(recovered),
            "drift_retries": self.drift_retries, "receipt": receipt,
        }
        return self._step(
            "cleanup_pending", "cleanup_pending", "publication_confirmed",
            publication=publication, target_after=self.integration_head, failure=None,
        )

    def cleanup_blocked(self, component, receipt):
        if self.phase != "cleanup_pending" or component not in self.cleanup:
            raise DomainError("Cleanup blockage does not match the integration state")
        cleanup = dict(self.cleanup)
        cleanup[component] = "blocked"
        details = {"component": component, "receipt": receipt}
        return self._step(
            "cleanup_pending", "cleanup_pending", f"{component}_cleanup_blocked",
            details=details, cleanup=cleanup,
        )

    def configuration_backup_completed(self, files):
        if self.phase != "cleanup_pending":
            raise DomainError("Configuration backup requires confirmed publication")
        return self._step(
            "cleanup_pending", "cleanup_pending", "configuration_backup_completed",
            details={"files": list(files)},
        )

    def cleanup_completed(self, component, outcome):
        if component not in self.cleanup or outcome not in ("removed", "deleted"):
            raise DomainError("Cleanup outcome does not match the integration state")
        if self.cleanup[component] == outcome and self.phase in ("cleanup_pending", "integrated"):
            return self
        if self.phase != "cleanup_pending":
            raise DomainError("Cleanup step does not match the integration state")
        cleanup = dict(self.cleanup)
        cleanup[component] = outcome
        terminal = cleanup == {
            "task_worktree": "removed", "task_branch": "deleted",
            "temporary_backups": "removed",
        }
        return self._step(
            "integrated" if terminal else "cleanup_pending",
            "integrated" if terminal else "cleanup_pending",
            f"{component}_{outcome}", cleanup=cleanup,
        )

    def to_storage(self):
        return {
            "kind": "result_integration", "schema": _SCHEMA,
            "intent": self.intent.identity(), "status": self.status, "phase": self.phase,
            "version": self.version, "accepted_commit": self.accepted_commit,
            "task_branch": self.task_branch, "task_worktree": self.task_worktree,
            "temporary_backup_directory": self.temporary_backup_directory,
            "last_included_target": self.last_included_target,
            "integration_head": self.integration_head, "target_after": self.target_after,
            "conflicts": list(self.conflicts), "resolutions": list(self.resolutions),
            "checks": list(self.checks), "publication": self.publication,
            "temporary_backups": list(self.temporary_backups), "failure": self.failure,
            "cleanup": self.cleanup, "history": list(self.history),
            "drift_retries": self.drift_retries,
        }

    def result(self, replayed=False):
        return {
            "status": self.status, "task": self.intent.task_id,
            "request_id": self.intent.request_id,
            "source_commit": self.intent.expected_source_commit,
            "accepted_commit": self.accepted_commit, "task_branch": self.task_branch,
            "task_worktree": self.task_worktree,
            "temporary_backup_directory": self.temporary_backup_directory,
            "temporary_backups": list(self.temporary_backups), "phase": self.phase,
            "last_included_target": self.last_included_target,
            "integration_head": self.integration_head,
            "target_before": self.intent.expected_target_commit,
            "target_after": self.target_after, "conflicts": list(self.conflicts),
            "resolutions": list(self.resolutions), "checks": list(self.checks),
            "publication": self.publication, "cleanup": self.cleanup,
            "failure": self.failure, "history": list(self.history), "replayed": replayed,
        }
