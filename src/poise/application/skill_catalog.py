"""Stateless compositions over the shared catalog and existing Task validator."""
from collections import Counter
from copy import deepcopy

from ..modules.foundation.errors import DomainError
from ..modules.tasks.decomposition import FocusedDecomposition
from ..modules.verification.domain import exact_keys


def project_policy(catalog, skill_ids, areas):
    # Embed a validated snapshot, not a mutable path or a second classification.
    catalog.select(skill_ids)
    policy = {"catalog": catalog.as_dict(), "skill_ids": sorted(skill_ids), "areas": deepcopy(areas)}
    FocusedDecomposition.validate_policy(policy)
    policy["areas"].sort(key=lambda item: item["path"])
    return policy


def validate_tasks(tasks, policy):
    """Validate every submitted declaration; do not create or transition Tasks."""
    if not isinstance(tasks, list) or not tasks:
        raise DomainError("tasks must be a non-empty list")
    names = [task.get("id") for task in tasks if isinstance(task, dict) and isinstance(task.get("id"), str)]
    duplicates = {name for name, count in Counter(names).items() if count > 1}
    results = []
    for index, task in enumerate(tasks):
        task_id = task.get("id") if isinstance(task, dict) else None
        label = task_id if isinstance(task_id, str) and task_id.strip() else f"$[{index}]"
        issues = []
        try:
            exact_keys(task, {"id", "stages", "decomposition"}, f"tasks[{label}]")
            if not isinstance(task_id, str) or not task_id.strip():
                raise DomainError("task id must be a non-empty string")
        except DomainError as exc:
            issues.append({"code": "task.fields", "location": f"tasks[{label}]", "message": str(exc)})
        if label in duplicates:
            issues.append({"code": "task.duplicate", "location": f"tasks[{label}]", "message": "duplicate task ID in batch"})
        if isinstance(task, dict) and "decomposition" in task and "stages" in task:
            issues.extend(FocusedDecomposition.inspect(task["decomposition"], task["stages"], policy))
        issues.sort(key=lambda item: (item["location"], item["code"], item["message"]))
        results.append({"task": label, "valid": not issues, "diagnostics": issues})
    return sorted(results, key=lambda item: item["task"])
