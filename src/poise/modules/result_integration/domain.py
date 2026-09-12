from __future__ import annotations

from dataclasses import dataclass, replace
import re

from ..foundation.errors import DomainError
from ..task_cleanup.domain import CleanupIntent, CleanupRun, CommitDisposition


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
    version: int
    target_before: str | None
    target_after: str | None
    conflicts: tuple[str, ...]
    resolutions: tuple[dict, ...]
    merge: dict | None
    failure: dict | None
    cleanup: CleanupRun | None
    history: tuple[dict, ...]

    @classmethod
    def new(cls, intent):
        return cls(intent, "prepared", 0, None, None, (), (), None, None,
                   None,
                   ({"status": "prepared"},))

    @classmethod
    def restore(cls, value):
        if not isinstance(value, dict) or value.get("kind") != "result_integration":
            raise DomainError("Saved result integration state is invalid")
        intent = IntegrationIntent.parse({**value["intent"], "resolutions": []})
        return cls(intent, value["status"], value["version"], value["target_before"],
                   value["target_after"], tuple(value["conflicts"]), tuple(value["resolutions"]),
                   value["merge"], value["failure"],
                   None if value["cleanup"] is None else CleanupRun.restore(value["cleanup"]),
                   tuple(value["history"]))

    def _step(self, status, event=None, details=None, **changes):
        entry = {"status": status}
        if event is not None:
            entry["event"] = event
        if details is not None:
            entry["details"] = details
        return replace(self, status=status, version=self.version + 1,
                       history=self.history + (entry,), **changes)

    def start(self, observation):
        if self.status != "prepared":
            raise DomainError("Only a prepared integration can start")
        target = _commit(observation.get("target_before"), "observed target")
        if target != self.intent.expected_target_commit:
            raise DomainError("Observed target changed before integration")
        preflight = observation.get("preflight")
        merge = None if preflight is None else {"preflight": preflight}
        return self._step("running", "merge_started", target_before=target, merge=merge)

    def await_resolution(self, conflicts, receipt):
        paths = tuple(sorted(conflicts))
        if self.status != "running" or not paths:
            raise DomainError("A running integration requires observed conflicts")
        return self._step("awaiting_resolution", "conflicts_observed", conflicts=paths,
                          merge={**(self.merge or {}), "receipt": receipt,
                                 "conflicts": list(paths), "resolutions": []})

    def continue_with(self, resolutions):
        if self.status != "awaiting_resolution":
            raise DomainError("No conflict continuation is pending")
        parsed = tuple(_resolution(value) for value in resolutions)
        if {item["path"] for item in parsed} != set(self.conflicts):
            raise DomainError("Provide one resolution for every observed conflict, and no unrelated paths")
        return self._step("running", "resolutions_declared", resolutions=parsed,
                          merge={**self.merge, "resolutions": list(parsed)})

    def integrated(self, target_after, receipt, resources=None):
        if self.status != "running":
            raise DomainError("Only a running integration can record a target result")
        target = _commit(target_after, "integrated target")
        merge = {"receipt": receipt, "conflicts": list(self.conflicts),
                 "resolutions": list(self.resolutions)}
        if resources is None:
            resources = [
                {"kind": "worktree", "identity": self.intent.task_id, "path": self.intent.task_id},
                {"kind": "branch", "name": self.intent.task_id, "commit": self.intent.expected_source_commit},
            ]
        disposition = CommitDisposition("integrated", self.intent.expected_source_commit,
                                        target, self.intent.request_id)
        cleanup_intent = CleanupIntent(self.intent.request_id, self.intent.task_id,
                                       self.intent.authorization, disposition)
        cleanup = CleanupRun.new(cleanup_intent, resources)
        status = "integrated" if cleanup.complete else "cleanup_pending"
        return self._step(status, "target_integrated", target_after=target,
                          merge=merge, failure=None, cleanup=cleanup)

    def blocked(self, reason, receipt):
        if self.status != "running":
            raise DomainError("Only a running integration can record a merge failure")
        failure = {"reason": _text(reason, "failure reason"), "receipt": receipt}
        return self._step("blocked", "merge_blocked", details=failure, failure=failure)

    def retry_blocked(self):
        if self.status != "blocked" or not isinstance(self.failure, dict):
            raise DomainError("No blocked integration can be retried")
        target = "running" if self.failure["reason"] == "integration_commit_failed" else "prepared"
        return self._step(target, "merge_retry", failure=None)

    def cleanup_blocked(self, component, receipt):
        if self.status != "cleanup_pending" or component not in ("worktree", "branch") or self.cleanup is None:
            raise DomainError("Cleanup blockage does not match the integration state")
        resource = next((item for item in self.cleanup.resources if item.kind == component), None)
        if resource is None: return self
        cleanup = self.cleanup.cleanup_blocked(resource, receipt)
        details = {"component": component, "receipt": receipt}
        return self._step("cleanup_pending", f"{component}_cleanup_blocked",
                          details=details, cleanup=cleanup)

    def worktree_removed(self):
        if self.status not in ("cleanup_pending", "integrated") or self.cleanup is None:
            raise DomainError("Worktree cleanup requires an integrated target")
        resource = next((item for item in self.cleanup.resources if item.kind == "worktree"), None)
        if resource is None:
            return self
        cleanup = self.cleanup.retry_blocked() if self.cleanup.blocker is not None else self.cleanup
        cleanup = cleanup.resource_removed(resource)
        return self._step(self.status, "worktree_removed", cleanup=cleanup)

    def branch_deleted(self):
        if self.cleanup is None:
            raise DomainError("Cleanup state is missing")
        if any(item.kind == "worktree" for item in self.cleanup.resources):
            raise DomainError("Branch deletion follows worktree removal")
        resource = next((item for item in self.cleanup.resources if item.kind == "branch"), None)
        if resource is None:
            return self
        cleanup = self.cleanup.retry_blocked() if self.cleanup.blocker is not None else self.cleanup
        cleanup = cleanup.resource_removed(resource)
        return self._step("integrated", "branch_deleted", cleanup=cleanup)

    def to_storage(self):
        return {
            "kind": "result_integration",
            "intent": self.intent.identity(),
            "status": self.status,
            "version": self.version,
            "target_before": self.target_before,
            "target_after": self.target_after,
            "conflicts": list(self.conflicts),
            "resolutions": list(self.resolutions),
            "merge": self.merge,
            "failure": self.failure,
            "cleanup": None if self.cleanup is None else self.cleanup.to_storage(),
            "history": list(self.history),
        }

    def result(self, replayed=False):
        value = {
            "status": self.status,
            "task": self.intent.task_id,
            "request_id": self.intent.request_id,
            "source_commit": self.intent.expected_source_commit,
            "target_before": self.target_before,
            "target_after": self.target_after,
            "cleanup": None if self.cleanup is None else {
                key:value for key,value in self.cleanup.result().items()
                if key not in ("task", "history")
            },
            "merge": self.merge,
            "failure": self.failure,
            "history": list(self.history),
            "replayed": replayed,
        }
        if self.status == "awaiting_resolution":
            value["conflicts"] = list(self.conflicts)
        return value
