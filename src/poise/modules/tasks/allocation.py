"""Pure Task identity policy and creation-intent semantics."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

from ..foundation.errors import DomainError


def _exact(value, keys, where):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise DomainError(f"task_ids {where}: required exact fields {sorted(keys)}")


def _integer(value, where, *, positive=False):
    if type(value) is not int or (positive and value <= 0):
        kind = "positive integer" if positive else "integer"
        raise DomainError(f"task_ids {where}: required {kind}")
    return value


def _identifier(value, where):
    if (
        not isinstance(value, str)
        or not value.strip()
        or value in (".", "..")
        or any(char in value for char in ("/", "\\", "\x00"))
    ):
        raise DomainError(f"{where}: required one nonempty path component")
    return value


@dataclass(frozen=True)
class TaskIdPolicy:
    minimum: int
    maximum: int
    width: int
    first: int
    step: int

    @classmethod
    def parse(cls, value):
        _exact(value, {"namespace", "width", "progression"}, "policy")
        _exact(value["namespace"], {"minimum", "maximum"}, "namespace")
        _exact(value["progression"], {"first", "step"}, "progression")
        minimum = _integer(value["namespace"]["minimum"], "namespace.minimum")
        maximum = _integer(value["namespace"]["maximum"], "namespace.maximum")
        width = _integer(value["width"], "width", positive=True)
        first = _integer(value["progression"]["first"], "progression.first")
        step = _integer(value["progression"]["step"], "progression.step", positive=True)
        if minimum < 0 or minimum > maximum:
            raise DomainError("task_ids namespace: minimum must be nonnegative and not exceed maximum")
        if not minimum <= first <= maximum:
            raise DomainError("task_ids progression.first must belong to the namespace")
        if len(str(maximum)) > width:
            raise DomainError("task_ids width cannot represent the configured namespace")
        return cls(minimum, maximum, width, first, step)

    def candidates(self):
        candidate = self.first
        while candidate <= self.maximum:
            if candidate >= self.minimum:
                yield str(candidate).zfill(self.width)
            candidate += self.step


@dataclass(frozen=True)
class Allocation:
    request_id: str | None
    task_id: str
    digest: str | None
    replayed: bool

    def receipt(self):
        if self.request_id is None:
            return None
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "replayed": self.replayed,
        }


def creation_parts(intent):
    if not isinstance(intent, dict):
        raise DomainError("Task creation intent must be an object")
    if set(intent) == {"request_id", "task"}:
        request_id = _identifier(intent["request_id"], "creation request_id")
        task = intent["task"]
        if not isinstance(task, dict) or "id" in task:
            raise DomainError("Automatic task intent requires task without id")
        packed = json.dumps(
            intent,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return request_id, deepcopy(task), hashlib.sha256(packed.encode()).hexdigest()
    if "id" not in intent:
        raise DomainError("Task creation requires an explicit id or automatic request_id intent")
    return None, deepcopy(intent), None


def creation_alias(intent):
    request_id, task, _ = creation_parts(intent)
    return request_id if request_id is not None else _identifier(task["id"], "task id")


def materialize_contract(intent, task_id):
    request_id, task, request_digest = creation_parts(intent)
    if request_id is None:
        if task["id"] != task_id:
            raise DomainError("Explicit Task identity changed during creation")
        return task, None
    return {"id": task_id, **task}, {
        "request_id": request_id,
        "digest": request_digest,
    }
