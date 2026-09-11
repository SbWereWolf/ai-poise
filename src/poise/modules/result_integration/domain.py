from __future__ import annotations

from dataclasses import dataclass, replace
import re

from ..foundation.errors import DomainError


_COMMIT = re.compile(r"[0-9a-f]{40,64}")


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
        raise DomainError("Resolution path must stay inside the target worktree")
    return {"path": path, "resolution": _text(value["resolution"], "resolution rationale")}


@dataclass(frozen=True)
class IntegrationIntent:
    request_id: str
    task_id: str
    expected_source_commit: str
    expected_target_commit: str
    authorization: str
    resolutions: tuple[dict, ...]

    @classmethod
    def parse(cls, value):
        keys = {"request_id", "task_id", "expected_source_commit", "expected_target_commit",
                "authorization", "resolutions"}
        if not isinstance(value, dict) or set(value) != keys:
            raise DomainError("integrate input requires exact intent and resolutions fields")
        resolutions = value["resolutions"]
        if not isinstance(resolutions, list):
            raise DomainError("resolutions must be an explicit list")
        parsed = tuple(_resolution(item) for item in resolutions)
        if len({item["path"] for item in parsed}) != len(parsed):
            raise DomainError("Resolution paths must be unique")
        return cls(
            _text(value["request_id"], "request_id"),
            _text(value["task_id"], "task_id"),
            _commit(value["expected_source_commit"], "expected_source_commit"),
            _commit(value["expected_target_commit"], "expected_target_commit"),
            _text(value["authorization"], "authorization"),
            parsed,
        )

    def identity(self):
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "expected_source_commit": self.expected_source_commit,
            "expected_target_commit": self.expected_target_commit,
            "authorization": self.authorization,
        }


@dataclass(frozen=True)
class IntegrationRun:
    intent: IntegrationIntent
    status: str
    phase: str
    version: int
    accepted_commit: str
    integration_branch: str
    integration_worktree: str
    temporary_backup_directory: str
    observed_target: str | None
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

    @classmethod
    def new(cls, intent, integration_branch, integration_worktree,
            temporary_backup_directory):
        cleanup = {
            "task_worktree": "pending",
            "integration_worktree": "pending",
            "task_branch": "pending",
            "integration_branch": "pending",
            "temporary_backups": "pending",
        }
        return cls(
            intent=intent,
            status="prepared",
            phase="prepared",
            version=0,
            accepted_commit=intent.expected_source_commit,
            integration_branch=_text(integration_branch, "integration branch"),
            integration_worktree=_text(integration_worktree, "integration worktree"),
            temporary_backup_directory=_text(
                temporary_backup_directory, "temporary backup directory"
            ),
            observed_target=None,
            integration_head=None,
            target_after=None,
            conflicts=(),
            resolutions=(),
            checks=(),
            publication=None,
            temporary_backups=(),
            failure=None,
            cleanup=cleanup,
            history=({"status": "prepared", "phase": "prepared"},),
            drift_retries=0,
        )

    @classmethod
    def restore(cls, value):
        if not isinstance(value, dict) or value.get("kind") != "result_integration":
            raise DomainError("Saved result integration state is invalid")
        intent = IntegrationIntent.parse({**value["intent"], "resolutions": []})
        return cls(
            intent=intent,
            status=value["status"],
            phase=value["phase"],
            version=value["version"],
            accepted_commit=value["accepted_commit"],
            integration_branch=value["integration_branch"],
            integration_worktree=value["integration_worktree"],
            temporary_backup_directory=value["temporary_backup_directory"],
            observed_target=value["observed_target"],
            integration_head=value["integration_head"],
            target_after=value["target_after"],
            conflicts=tuple(value["conflicts"]),
            resolutions=tuple(value["resolutions"]),
            checks=tuple(value["checks"]),
            publication=value["publication"],
            temporary_backups=tuple(value["temporary_backups"]),
            failure=value["failure"],
            cleanup=dict(value["cleanup"]),
            history=tuple(value["history"]),
            drift_retries=value["drift_retries"],
        )

    def _step(self, status, phase, event=None, details=None, **changes):
        entry = {"status": status, "phase": phase}
        if event is not None:
            entry["event"] = event
        if details is not None:
            entry["details"] = details
        return replace(self, status=status, phase=phase, version=self.version + 1,
                       history=self.history + (entry,), **changes)

    def begin_candidate(self, observed_target):
        if self.status not in ("prepared", "running") or self.phase not in (
            "prepared", "candidate_ready", "publishing"
        ):
            raise DomainError("Integration cannot start a candidate from this phase")
        target = _commit(observed_target, "observed target")
        return self._step(
            "running",
            "preparing_candidate",
            "candidate_started",
            observed_target=target,
            integration_head=target,
            conflicts=(),
            resolutions=(),
            failure=None,
            publication=None,
        )

    def await_resolution(self, conflicts, receipt):
        paths = tuple(sorted(conflicts))
        if self.phase != "preparing_candidate" or not paths:
            raise DomainError("A running integration requires observed conflicts")
        return self._step(
            "awaiting_resolution",
            "awaiting_resolution",
            "conflicts_observed",
            details={"receipt": receipt},
            conflicts=paths,
        )

    def continue_with(self, resolutions):
        if self.phase != "awaiting_resolution":
            raise DomainError("No conflict continuation is pending")
        parsed = tuple(_resolution(value) for value in resolutions)
        if {item["path"] for item in parsed} != set(self.conflicts):
            raise DomainError("Provide one resolution for every observed conflict, and no unrelated paths")
        return self._step(
            "running",
            "preparing_candidate",
            "resolutions_declared",
            resolutions=parsed,
        )

    def candidate_ready(self, integration_head, receipt):
        if self.phase != "preparing_candidate":
            raise DomainError("Only a prepared candidate can record its head")
        head = _commit(integration_head, "integration head")
        return self._step(
            "running",
            "candidate_ready",
            "candidate_ready",
            details={"receipt": receipt},
            integration_head=head,
            failure=None,
        )

    def candidate_failed(self, reason, receipt):
        if self.phase != "preparing_candidate":
            raise DomainError("Only a running candidate can record a failure")
        failure = {
            "reason": _text(reason, "candidate failure reason"),
            "receipt": receipt,
        }
        return self._step(
            "blocked",
            "candidate_failed",
            "candidate_failed",
            details=failure,
            failure=failure,
        )

    def retry_candidate(self):
        if self.phase != "candidate_failed":
            raise DomainError("Only a failed candidate can be retried")
        return self._step(
            "running",
            "preparing_candidate",
            "candidate_retry_started",
            conflicts=(),
            resolutions=(),
            failure=None,
        )

    def checks_recorded(self, receipts):
        if self.phase != "candidate_ready":
            raise DomainError("Checks require a ready integration candidate")
        batch = tuple(receipts)
        passed = all(item.get("passed") is True for item in batch)
        checks = self.checks + batch
        if passed:
            return self._step(
                "running", "publishing", "checks_passed", checks=checks, failure=None
            )
        failure = {"reason": "checks_failed", "checks": list(batch)}
        return self._step(
            "blocked",
            "checks_failed",
            "checks_failed",
            details=failure,
            checks=checks,
            failure=failure,
        )

    def drifted(self, receipt):
        if self.phase != "publishing":
            raise DomainError("Target drift is observed only during publication")
        return self._step(
            "running",
            "publishing",
            "target_drifted",
            details={"receipt": receipt},
            drift_retries=self.drift_retries + 1,
        )

    def publication_confirmed(self, target_ref, receipt, *, no_op, recovered):
        if self.phase != "publishing" or self.integration_head is None \
                or self.observed_target is None:
            raise DomainError("Publication requires a tested candidate")
        publication = {
            "status": "confirmed",
            "target_ref": _text(target_ref, "target ref"),
            "commit": self.integration_head,
            "observed_target": self.observed_target,
            "no_op": bool(no_op),
            "recovered": bool(recovered),
            "drift_retries": self.drift_retries,
            "receipt": receipt,
        }
        return self._step(
            "cleanup_pending",
            "cleanup_pending",
            "publication_confirmed",
            publication=publication,
            target_after=self.integration_head,
            failure=None,
        )

    def cleanup_blocked(self, component, receipt):
        if self.phase != "cleanup_pending" or component not in self.cleanup:
            raise DomainError("Cleanup blockage does not match the integration state")
        cleanup = dict(self.cleanup)
        cleanup[component] = "blocked"
        details = {"component": component, "receipt": receipt}
        return self._step(
            "cleanup_pending",
            "cleanup_pending",
            f"{component}_cleanup_blocked",
            details=details,
            cleanup=cleanup,
        )

    def cleanup_completed(self, component, outcome):
        if component not in self.cleanup:
            raise DomainError("Cleanup step does not match the integration state")
        if outcome not in ("removed", "deleted"):
            raise DomainError("Cleanup outcome is invalid")
        if self.cleanup[component] == outcome and self.phase in (
            "cleanup_pending", "integrated"
        ):
            return self
        if self.phase != "cleanup_pending":
            raise DomainError("Cleanup step does not match the integration state")
        cleanup = dict(self.cleanup)
        cleanup[component] = outcome
        terminal = cleanup == {
            "task_worktree": "removed",
            "integration_worktree": "removed",
            "task_branch": "deleted",
            "integration_branch": "deleted",
            "temporary_backups": "removed",
        }
        return self._step(
            "integrated" if terminal else "cleanup_pending",
            "integrated" if terminal else "cleanup_pending",
            f"{component}_{outcome}",
            cleanup=cleanup,
        )

    def to_storage(self):
        return {
            "kind": "result_integration",
            "intent": self.intent.identity(),
            "status": self.status,
            "phase": self.phase,
            "version": self.version,
            "accepted_commit": self.accepted_commit,
            "integration_branch": self.integration_branch,
            "integration_worktree": self.integration_worktree,
            "temporary_backup_directory": self.temporary_backup_directory,
            "observed_target": self.observed_target,
            "integration_head": self.integration_head,
            "target_after": self.target_after,
            "conflicts": list(self.conflicts),
            "resolutions": list(self.resolutions),
            "checks": list(self.checks),
            "publication": self.publication,
            "temporary_backups": list(self.temporary_backups),
            "failure": self.failure,
            "cleanup": self.cleanup,
            "history": list(self.history),
            "drift_retries": self.drift_retries,
        }

    def result(self, replayed=False):
        value = {
            "status": self.status,
            "task": self.intent.task_id,
            "request_id": self.intent.request_id,
            "source_commit": self.intent.expected_source_commit,
            "accepted_commit": self.accepted_commit,
            "integration_branch": self.integration_branch,
            "integration_worktree": self.integration_worktree,
            "temporary_backup_directory": self.temporary_backup_directory,
            "temporary_backups": list(self.temporary_backups),
            "phase": self.phase,
            "observed_target": self.observed_target,
            "integration_head": self.integration_head,
            "target_before": self.intent.expected_target_commit,
            "target_after": self.target_after,
            "conflicts": list(self.conflicts),
            "resolutions": list(self.resolutions),
            "checks": list(self.checks),
            "publication": self.publication,
            "cleanup": self.cleanup,
            "merge": {
                "conflicts": list(self.conflicts),
                "resolutions": list(self.resolutions),
            },
            "failure": self.failure,
            "history": list(self.history),
            "replayed": replayed,
        }
        return value
