"""Pure stage-target decisions; persistence and transition writes stay in Task APIs."""

from dataclasses import dataclass

from ..foundation.errors import DomainError
from ..workflow.domain import HandlerKind
from .domain import Task, TaskStatus


@dataclass(frozen=True)
class ProgressionStep:
    kind: str
    current_stage: str
    target_stage: str | None
    next_stage: str | None = None
    from_role: str | None = None
    to_role: str | None = None


def crossed_role_boundary(task: Task, actor: str, history: dict | None) -> bool:
    """Prove ownership transfer of this verified result, retaining receiver self-cycles."""
    if (history is None
            or history["task_id"] != task.state.task_id
            or history["submission_digest"] != task.state.submission_digest
            or history["stage_id"] != task.stage.stage_id
            or history["iteration"] != task.state.iteration
            or history["through_version"] != task.state.version):
        raise DomainError("Current verified ownership provenance is unavailable or changed")
    events = history["events"]
    if (not events or events[0]["event"] != "verified"
            or [event["version"] for event in events] != list(
                range(events[0]["version"], task.state.version + 1))
            or any(event["submission"] != history["submission_id"]
                   or event["stage"] != task.stage.stage_id
                   or event["iteration"] != task.state.iteration for event in events)):
        raise DomainError("Current verified ownership history is incomplete or mismatched")
    owner = events[0]["actor"]
    if not isinstance(owner, str) or not owner.strip():
        raise DomainError("Current verified result has no proven author")
    crossed = False
    released_owner, released_crossed = None, False
    for event in events[1:]:
        kind, receiver = event["event"], event["actor"]
        if kind in ("handed_off", "ownership_released"):
            if owner is None or receiver is not None:
                raise DomainError("Current result release has no proven owner")
            released_owner, released_crossed = owner, crossed
            owner = None
        elif kind in ("handoff_resumed", "ownership_acquired"):
            if (owner is not None or released_owner is None
                    or not isinstance(receiver, str) or not receiver.strip()):
                raise DomainError("Current result acquisition has no proven release")
            crossed = released_crossed if receiver == released_owner else True
            owner = receiver
        elif kind == "user_accept":
            if owner is None or receiver != owner or task.state.status != TaskStatus.ACCEPTED:
                raise DomainError("Acceptance changed the current result ownership chain")
        else:
            raise DomainError("Current result history contains a non-ownership transition")
    if owner != actor or owner != task.state.claimed_by:
        raise DomainError("Current result history does not prove its actual owner")
    return crossed


def progression_step(task: Task, target_stage: str | None, crossed_role_boundary: bool) -> ProgressionStep:
    current = task.stage.stage_id
    try:
        reachable = target_stage is None or task.route.can_reach(current, target_stage)
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
    if following is None and target_stage is None:
        return ProgressionStep('task_acceptance_required', current, target_stage)
    if following is None or (target_stage is not None and not task.route.can_reach(following, target_stage)):
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
