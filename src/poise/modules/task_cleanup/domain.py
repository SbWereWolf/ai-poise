"""Crash-recoverable state for exact task-owned resource cleanup."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any

from ..foundation.errors import DomainError


_COMMIT = re.compile(r"[0-9a-f]{40,64}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _identifier(value: object, name: str) -> str:
    if (not isinstance(value, str) or not value or value in (".", "..")
            or "/" in value or "\\" in value or "\x00" in value):
        raise DomainError(f"cleanup {name} must be one nonempty path-safe component")
    return value


def _exact(value: object, keys: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise DomainError(f"{name} requires exact fields: {', '.join(sorted(keys))}")
    return value


@dataclass(frozen=True)
class CommitDisposition:
    kind: str
    expected_commit: str | None = None
    target_commit: str | None = None
    integration_request_id: str | None = None
    bundle_path: str | None = None

    @classmethod
    def parse(cls, value: object, *, internal: bool = False) -> "CommitDisposition":
        if value is None:
            raise DomainError("commit disposition is required")
        if not isinstance(value, dict):
            raise DomainError("commit disposition must be an object")
        kind = value.get("kind")
        public = {"discard_authorized", "preserved"}
        allowed = public | ({"integrated", "no_resources"} if internal else set())
        if kind not in allowed:
            raise DomainError("disposition kind must be preserved or discard_authorized")
        if kind in public:
            fields = {"kind", "expected_commit"}
            if internal and kind == "preserved" and "bundle_path" in value:
                fields.add("bundle_path")
            _exact(value, fields, "commit disposition")
            expected = value["expected_commit"]
            if not isinstance(expected, str) or _COMMIT.fullmatch(expected) is None:
                raise DomainError("expected_commit must be a full lowercase commit ID")
            bundle = value.get("bundle_path")
            if bundle is not None and (not isinstance(bundle, str) or not bundle):
                raise DomainError("bundle_path must be a nonempty string")
            return cls(kind, expected_commit=expected, bundle_path=bundle)
        if kind == "no_resources":
            _exact(value, {"kind"}, "commit disposition")
            return cls(kind)
        _exact(value, {"kind", "expected_commit", "target_commit", "integration_request_id"}, "commit disposition")
        if any(not isinstance(value[key], str) or not value[key] for key in value if key != "kind"):
            raise DomainError("integrated disposition requires exact commit and request identities")
        return cls(kind, value["expected_commit"], value["target_commit"], value["integration_request_id"])

    def to_dict(self) -> dict:
        out = {"kind": self.kind}
        for key in ("expected_commit", "target_commit", "integration_request_id", "bundle_path"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


@dataclass(frozen=True)
class CleanupIntent:
    request_id: str
    task_id: str
    authorization: str
    commit_disposition: CommitDisposition | None

    @classmethod
    def parse(cls, value: object, *, internal: bool = False) -> "CleanupIntent":
        data = _exact(value, {"request_id", "task_id", "authorization", "commit_disposition"}, "cleanup input")
        request_id = _identifier(data["request_id"], "request_id")
        task_id = _identifier(data["task_id"], "task_id")
        authorization = data["authorization"]
        if not isinstance(authorization, str) or not authorization.strip() or "\x00" in authorization:
            raise DomainError("cleanup authorization is required")
        disposition = None if data["commit_disposition"] is None else CommitDisposition.parse(data["commit_disposition"], internal=internal)
        return cls(request_id, task_id, authorization, disposition)

    def to_dict(self) -> dict:
        return {"request_id": self.request_id, "task_id": self.task_id,
                "authorization": self.authorization,
                "commit_disposition": None if self.commit_disposition is None else self.commit_disposition.to_dict()}


@dataclass(frozen=True)
class TaskOwnedResource:
    kind: str
    path: str | None = None
    identity: str | None = None
    name: str | None = None
    commit: str | None = None
    digest: str | None = None

    @classmethod
    def parse(cls, value: object) -> "TaskOwnedResource":
        if not isinstance(value, dict):
            raise DomainError("cleanup resource must be an object")
        kind = value.get("kind")
        shapes = {
            "worktree": {"kind", "identity", "path"},
            "branch": {"kind", "name", "commit"},
            "temporary": {"kind", "path", "digest"},
            "temporary_backup": {"kind", "path", "digest"},
        }
        if kind not in shapes:
            raise DomainError("unknown task-owned resource kind")
        _exact(value, shapes[kind], "cleanup resource")
        if any(not isinstance(v, str) or not v for k, v in value.items() if k != "kind"):
            raise DomainError("cleanup resource identities must be nonempty strings")
        if kind == "branch" and _COMMIT.fullmatch(value["commit"]) is None:
            raise DomainError("cleanup branch commit must be a full lowercase commit ID")
        if kind in ("temporary", "temporary_backup") and _DIGEST.fullmatch(value["digest"]) is None:
            raise DomainError("cleanup resource digest must be a lowercase sha256")
        return cls(kind, value.get("path"), value.get("identity"), value.get("name"), value.get("commit"), value.get("digest"))

    def to_dict(self, *, public: bool = False) -> dict:
        if self.kind == "worktree":
            out = {"kind": self.kind, "path": self.path}
            if not public: out["identity"] = self.identity
            return out
        if self.kind == "branch": return {"kind": self.kind, "name": self.name, "commit": self.commit}
        return {"kind": self.kind, "path": self.path, "digest": self.digest}


@dataclass(frozen=True)
class CleanupRun:
    intent: CleanupIntent
    resources: tuple[TaskOwnedResource, ...]
    version: int = 0
    removed: tuple[dict, ...] = ()
    blocker: dict | None = None
    history: tuple[dict, ...] = ()

    @classmethod
    def new(cls, intent: CleanupIntent, resources: list[dict] | tuple[dict, ...]) -> "CleanupRun":
        parsed = tuple(TaskOwnedResource.parse(item) for item in resources)
        if not parsed and intent.commit_disposition is None:
            intent = replace(intent, commit_disposition=CommitDisposition("no_resources"))
        return cls(intent, parsed, history=({"event": "no_task_resources"},) if not parsed else ())

    @classmethod
    def restore(cls, value: dict) -> "CleanupRun":
        fields = {"intent", "resources", "version", "removed", "blocker", "history"}
        if not isinstance(value, dict) or set(value) not in (fields, fields | {"kind"}):
            raise DomainError("Saved cleanup state requires exact fields")
        if "kind" in value and value["kind"] != "task_cleanup":
            raise DomainError("Saved cleanup state has an unknown kind")
        intent = CleanupIntent.parse(value["intent"], internal=True)
        if type(value["version"]) is not int or value["version"] < 0:
            raise DomainError("Saved cleanup version is invalid")
        if not isinstance(value["removed"], list) or not isinstance(value["history"], list):
            raise DomainError("Saved cleanup progress is invalid")
        if not isinstance(value["resources"], list):
            raise DomainError("Saved cleanup resources are invalid")
        if value["blocker"] is not None and not isinstance(value["blocker"], dict):
            raise DomainError("Saved cleanup blocker is invalid")
        return cls(intent, tuple(TaskOwnedResource.parse(x) for x in value["resources"]), value["version"],
                   tuple(value["removed"]), value["blocker"], tuple(value["history"]))

    def to_storage(self) -> dict:
        return {"intent": self.intent.to_dict(), "resources": [x.to_dict() for x in self.resources], "version": self.version,
                "removed": list(self.removed), "blocker": self.blocker, "history": list(self.history)}

    @property
    def status(self) -> str:
        if self.blocker is not None: return "cleanup_blocked"
        if self.intent.commit_disposition is None: return "disposition_required"
        return "cleanup_complete" if not self.resources else "cleanup_pending"

    @property
    def complete(self) -> bool:
        return self.status == "cleanup_complete"

    @property
    def disposition(self) -> CommitDisposition | None:
        return self.intent.commit_disposition

    def remaining_resources(self) -> tuple[dict, ...]:
        return tuple(x.to_dict() for x in self.resources)

    def with_disposition(self, disposition: CommitDisposition) -> "CleanupRun":
        existing = self.intent.commit_disposition
        if existing is not None and existing.to_dict() != disposition.to_dict():
            raise DomainError("Cleanup request identity has immutable commit disposition")
        return replace(self, intent=replace(self.intent, commit_disposition=disposition), blocker=None,
                       version=self.version + 1)

    def with_bundle(self, path: str) -> "CleanupRun":
        disposition = self.intent.commit_disposition
        if disposition is None or disposition.kind != "preserved":
            raise DomainError("Bundle belongs only to preserved disposition")
        return replace(self, intent=replace(self.intent, commit_disposition=replace(disposition, bundle_path=path)),
                       version=self.version + 1,
                       history=self.history + ({"event": "commit_preserved", "bundle_path": path},))

    def cleanup_blocked(self, resource: dict | TaskOwnedResource, receipt: dict) -> "CleanupRun":
        owned = resource if isinstance(resource, TaskOwnedResource) else TaskOwnedResource.parse(resource)
        reason = receipt.get("reason", f"{owned.kind}_cleanup_failed")
        blocker = {"reason": reason, "resource": owned.to_dict(public=True),
                   "recovery": receipt.get("recovery", "Resolve the reported ownership or resource lock and replay this cleanup request.")}
        for key in ("expected_commit", "actual_commit"):
            if key in receipt: blocker[key] = receipt[key]
        event = {"event": f"{owned.kind}_cleanup_blocked", "receipt": receipt}
        return replace(self, blocker=blocker, version=self.version + 1, history=self.history + (event,))

    def retry_blocked(self) -> "CleanupRun":
        return replace(self, blocker=None, version=self.version + 1)

    def checkpointed(self, branch: TaskOwnedResource, intent: CleanupIntent) -> "CleanupRun":
        if self.blocker is None or self.blocker.get("reason") != "dirty_worktree_requires_decision":
            raise DomainError("Only a dirty-worktree blocker can accept a checkpoint")
        previous = next((item for item in self.resources if item.kind == "branch"), None)
        if previous is None or branch.kind != "branch" or branch.name != previous.name:
            raise DomainError("Checkpoint must preserve the exact task branch identity")
        if branch.commit == previous.commit:
            raise DomainError("Checkpoint must advance the terminal task commit")
        if intent.task_id != self.intent.task_id or intent.commit_disposition is None:
            raise DomainError("Checkpoint requires an explicit disposition for the same Task")
        if intent.commit_disposition.expected_commit != branch.commit:
            raise DomainError("Checkpoint disposition must name the exact checkpoint commit")
        if self.intent.commit_disposition is not None and intent.request_id == self.intent.request_id:
            raise DomainError("A changed checkpoint commit requires a new cleanup request identity")
        resources = tuple(branch if item == previous else item for item in self.resources)
        event = {"event": "worktree_checkpointed", "branch": branch.name,
                 "previous_commit": previous.commit, "commit": branch.commit,
                 "request_id": intent.request_id}
        return replace(self, intent=intent, resources=resources, blocker=None,
                       version=self.version + 1, history=self.history + (event,))

    def resource_removed(self, resource: dict | TaskOwnedResource, receipt: dict | None = None) -> "CleanupRun":
        if self.intent.commit_disposition is None:
            raise DomainError("Commit disposition must be resolved before cleanup")
        owned = resource if isinstance(resource, TaskOwnedResource) else TaskOwnedResource.parse(resource)
        if owned not in self.resources: return self
        if owned.kind == "branch" and any(x.kind == "worktree" for x in self.resources):
            raise DomainError("Worktree must be removed before its branch")
        remaining = tuple(x for x in self.resources if x != owned)
        event = {"event": f"{owned.kind}_removed"}
        if receipt is not None: event["receipt"] = receipt
        return replace(self, resources=remaining, removed=self.removed + (owned.to_dict(),), version=self.version + 1,
                       blocker=None, history=self.history + (event,))

    def result(self, *, replayed: bool = False) -> dict:
        disposition = self.intent.commit_disposition
        remaining = [x.to_dict(public=True) for x in self.resources]
        out: dict[str, Any] = {"status": self.status, "task": self.intent.task_id,
                              "remaining_resources": remaining}
        commit = next((x.commit for x in self.resources if x.kind == "branch"), None)
        if commit is None and disposition is not None: commit = disposition.expected_commit
        if self.status == "disposition_required":
            out["commit"] = commit
            out["blocker"] = {"reason": "commit_disposition_required",
                              "recovery": "Invoke cleanup with an explicit preserved or discard_authorized disposition."}
        else:
            out["disposition"] = disposition.to_dict() if disposition is not None else None
            out["history"] = list(self.history)
            if self.blocker is not None: out["blocker"] = self.blocker
        if replayed: out["replayed"] = True
        return out
