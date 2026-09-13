"""The single owner of standalone and Sprint-member Task artifact locations."""

from pathlib import Path

from ..common import descendant


SPRINT_TASK_DIRECTORY = "task"


def sprint_root(state: Path, paths: dict, sprint_id: str) -> Path:
    return descendant(descendant(state, paths["sprints"]), sprint_id)


def task_root(state: Path, paths: dict, task_id: str, sprint_id: str | None) -> Path:
    if sprint_id is None:
        return descendant(descendant(state, paths["standalone_tasks"]), task_id)
    members = descendant(sprint_root(state, paths, sprint_id), SPRINT_TASK_DIRECTORY)
    return descendant(members, task_id)
