"""The retired replacement writer and readable historical replacement relations."""

from dataclasses import replace

import pytest

from batch.helpers import request
from conftest import WorkPoise as Poise
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from poise.modules.sprints.domain import Sprint
from sprints.helpers import draft, publish, setup, task


def test_public_replace_task_action_is_retired(project):
    setup(project)
    tools = WorkTools(Poise(project["config_path"], "planner"))
    planned = draft(tools, [task(project, "BROKEN")])
    current = publish(tools, planned["revision"])
    before = tools.runtime.sprint_tools.query("S", "plan")

    with pytest.raises(PoiseError, match="Unknown sprint action"):
        tools.invoke(request("sprint", {
            "action": "replace_task",
            "sprint_id": "S",
            "request_id": "retired-replacement",
            "expected_revision": current["revision"],
            "source_task": "BROKEN",
            "replacement": task(project, "BROKEN-2"),
            "reason": "The contract is broken.",
            "authorization": "User authorized repair.",
        }))

    assert tools.runtime.sprint_tools.query("S", "plan") == before


def test_historical_replacement_relation_remains_readable(project):
    setup(project)
    runtime = Poise(project["config_path"], "planner")
    tools = WorkTools(runtime)
    planned = draft(tools, [task(project, "OLD")])
    publish(tools, planned["revision"])
    with runtime.store.unit_of_work() as unit:
        record = unit.sprints.get("S")
        sprint = Sprint.restore(record["aggregate"])
        relation = {
            "kind": "task_replacement",
            "source": "OLD",
            "replacement": "NEW",
            "reason": "Historical correction",
            "authorization": "Historical user decision",
            "from_revision": sprint.revision,
            "to_revision": sprint.revision + 1,
            "safety": {"kind": "available"},
        }
        historical = replace(
            sprint,
            revision=sprint.revision + 1,
            decisions=sprint.decisions + (relation,),
        )
        unit.sprints.save(
            {**record, "aggregate": historical.to_dict()}, sprint.revision
        )

    current = runtime.sprint_tools.query("S", "current")
    assert "replacements" not in current
    history = runtime.sprint_tools.query("S", "history")
    assert "Historical correction" in str(history)
    assert "task_replacement" in str(history)
