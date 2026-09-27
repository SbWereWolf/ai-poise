"""The single owner of standalone and Sprint-member Task artifact locations."""

from pathlib import Path

from ..common import PoiseError, descendant


SPRINT_TASK_DIRECTORY = "task"
ARTIFACT_DRAFT_HISTORY_DIRECTORY = "artifact-draft-history"


def sprint_root(state: Path, paths: dict, sprint_id: str) -> Path:
    return descendant(descendant(state, paths["sprints"]), sprint_id)


def task_root(state: Path, paths: dict, task_id: str, sprint_id: str | None) -> Path:
    if sprint_id is None:
        owner_home = descendant(state, paths["standalone_tasks"])
    else:
        owner_home = descendant(sprint_root(state, paths, sprint_id), SPRINT_TASK_DIRECTORY)
    candidate = owner_home / task_id
    if candidate.is_symlink():
        raise PoiseError("Корень владельца Task не может быть symlink")
    return descendant(owner_home, task_id)
