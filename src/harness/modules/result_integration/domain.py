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
    version: int
    target_before: str | None
    target_after: str | None
    conflicts: tuple[str, ...]
    resolutions: tuple[dict, ...]
    merge: dict | None
    cleanup: dict
    history: tuple[dict, ...]

    @classmethod
    def new(cls, intent):
        return cls(intent, "prepared", 0, None, None, (), (), None,
                   {"worktree": "pending", "branch": "pending"},
                   ({"status": "prepared"},))

    @classmethod
    def restore(cls, value):
        if not isinstance(value, dict) or value.get("kind") != "result_integration":
            raise DomainError("Saved result integration state is invalid")
        intent = IntegrationIntent.parse({**value["intent"], "resolutions": []})
        return cls(intent, value["status"], value["version"], value["target_before"],
                   value["target_after"], tuple(value["conflicts"]), tuple(value["resolutions"]),
                   value["merge"], dict(value["cleanup"]), tuple(value["history"]))

    def _step(self, status, event=None, **changes):
        entry = {"status": status}
        if event is not None:
            entry["event"] = event
        return replace(self, status=status, version=self.version + 1,
                       history=self.history + (entry,), **changes)

    def start(self, observation):
        if self.status != "prepared":
            raise DomainError("Only a prepared integration can start")
        target = _commit(observation.get("target_before"), "observed target")
        if target != self.intent.expected_target_commit:
            raise DomainError("Observed target changed before integration")
        return self._step("running", "merge_started", target_before=target)

    def await_resolution(self, conflicts, receipt):
        paths = tuple(sorted(conflicts))
        if self.status != "running" or not paths:
            raise DomainError("A running integration requires observed conflicts")
        return self._step("awaiting_resolution", "conflicts_observed", conflicts=paths,
                          merge={"receipt": receipt, "conflicts": list(paths), "resolutions": []})

    def continue_with(self, resolutions):
        if self.status != "awaiting_resolution":
            raise DomainError("No conflict continuation is pending")
        parsed = tuple(_resolution(value) for value in resolutions)
        if {item["path"] for item in parsed} != set(self.conflicts):
            raise DomainError("Provide one resolution for every observed conflict, and no unrelated paths")
        return self._step("running", "resolutions_declared", resolutions=parsed,
                          merge={**self.merge, "resolutions": list(parsed)})

    def integrated(self, target_after, receipt):
        if self.status != "running":
            raise DomainError("Only a running integration can record a target result")
        target = _commit(target_after, "integrated target")
        merge = {"receipt": receipt, "conflicts": list(self.conflicts),
                 "resolutions": list(self.resolutions)}
        return self._step("cleanup_pending", "target_integrated", target_after=target, merge=merge)

    def cleanup_blocked(self, component, receipt):
        if self.status != "cleanup_pending" or component not in ("worktree", "branch"):
            raise DomainError("Cleanup blockage does not match the integration state")
        cleanup = dict(self.cleanup)
        cleanup[component] = "blocked"
        return self._step("cleanup_pending", f"{component}_cleanup_blocked", cleanup=cleanup)

    def worktree_removed(self):
        if self.status not in ("cleanup_pending", "integrated"):
            raise DomainError("Worktree cleanup requires an integrated target")
        if self.cleanup["worktree"] == "removed":
            return self
        cleanup = dict(self.cleanup)
        cleanup["worktree"] = "removed"
        return self._step(self.status, "worktree_removed", cleanup=cleanup)

    def branch_deleted(self):
        if self.cleanup["worktree"] != "removed":
            raise DomainError("Branch deletion follows worktree removal")
        if self.cleanup["branch"] == "deleted":
            return self
        cleanup = dict(self.cleanup)
        cleanup["branch"] = "deleted"
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
            "cleanup": self.cleanup,
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
            "cleanup": self.cleanup,
            "merge": self.merge,
            "history": list(self.history),
            "replayed": replayed,
        }
        if self.status == "awaiting_resolution":
            value["conflicts"] = list(self.conflicts)
        return value

