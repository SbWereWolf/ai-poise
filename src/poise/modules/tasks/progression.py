"""Pure stage-target decisions; persistence and transition writes stay in Task APIs."""

from dataclasses import dataclass

from ..foundation.errors import DomainError
from ..workflow.domain import HandlerKind
from .domain import Task, TaskStatus


@dataclass(frozen=True)
class ProgressionStep:
    kind: str
    current_stage: str
    target_stage: str
    next_stage: str | None = None
    from_role: str | None = None
    to_role: str | None = None


def progression_step(task: Task, target_stage: str, crossed_role_boundary: bool) -> ProgressionStep:
    current = task.stage.stage_id
    try:
        reachable = task.route.can_reach(current, target_stage)
    except DomainError as exc:
        raise DomainError("Progression target is not reachable by ordinary transitions") from exc
    if not reachable:
        raise DomainError("Progression target is not reachable by ordinary transitions")
    if current == target_stage:
        return ProgressionStep("target_reached", current, target_stage)
    if task.state.status == TaskStatus.ACTIVE:
        return ProgressionStep("work_required", current, target_stage)
    if task.state.status not in (TaskStatus.VERIFIED, TaskStatus.ACCEPTED):
        raise DomainError("Progression requires active work or a verified stage result")
    if task.progress.outcome is None:
        raise DomainError("Verified progression requires an exact stage outcome")
    following = task.route.node(current).target(task.progress.outcome)
    if following is None or not task.route.can_reach(following, target_stage):
        raise DomainError("Progression target is not reachable by ordinary transitions")
    current_role = task.route.node(current).role
    following_role = task.route.node(following).role
    if current_role != following_role and not crossed_role_boundary:
        return ProgressionStep(
            "role_handoff_required",
            current,
            target_stage,
            following,
            current_role,
            following_role,
        )
    if task.route.node(following).handler == HandlerKind.PUBLISH:
        return ProgressionStep(
            "user_acceptance_required",
            current,
            target_stage,
            following,
            current_role,
            following_role,
        )
    return ProgressionStep(
        "advance",
        current,
        target_stage,
        following,
        current_role,
        following_role,
    )
