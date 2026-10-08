"""Current Task lifecycle derived from retained native restart audit."""
from dataclasses import dataclass

from ..foundation.errors import DomainError


def invalid_audit(detail: str) -> DomainError:
    return DomainError(f"Invalid Task lifecycle audit: {detail}")


@dataclass(frozen=True)
class TaskLifecycle:
    boundary: int | None

    def __post_init__(self):
        if self.boundary is not None and (
            type(self.boundary) is not int or self.boundary < 0
        ):
            raise invalid_audit("from_version must be a nonnegative integer")

    @classmethod
    def from_audit(cls, current_version, history, restart_versions):
        if type(current_version) is not int or current_version < 0:
            raise invalid_audit("current Task version must be a nonnegative integer")
        if not isinstance(history, list):
            raise invalid_audit("restart_history must be a list")
        boundaries = []
        for record in history:
            if not isinstance(record, dict):
                raise invalid_audit("restart_history records must be objects")
            boundary = record.get("from_version")
            if type(boundary) is not int or not 0 <= boundary < current_version:
                raise invalid_audit("from_version must precede the current Task version")
            if boundaries and boundary <= boundaries[-1]:
                raise invalid_audit("restart boundaries must strictly increase")
            boundaries.append(boundary)
        previous = None
        for version in restart_versions:
            if type(version) is not int or version <= 0 or version > current_version:
                raise invalid_audit("native restart version must be positive and not future")
            if previous is not None and version <= previous:
                raise invalid_audit("native restart versions must strictly increase")
            if version - 1 not in boundaries:
                raise invalid_audit("native restart is missing from restart_history")
            previous = version
        if previous is not None and boundaries[-1] != previous - 1:
            raise invalid_audit("latest restart boundary contradicts the native audit")
        return cls(boundaries[-1] if boundaries else None)
