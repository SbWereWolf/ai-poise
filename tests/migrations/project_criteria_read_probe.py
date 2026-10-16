"""Fresh-process integration probe of the public work query after migration."""
from contextlib import contextmanager, ExitStack, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

from poise.application.work import WorkTools
from poise.runtime import Poise
from poise.modules.accounting.clock import ClockObservation


class FixtureClock:
    def observe(self):
        return ClockObservation("2026-10-11T12:00:00+00:00", 1, "criteria-migration-fixture")


def migration_lock_probe():
    from poise.__main__ import main as cli
    from poise.infrastructure import locking, project_config, stage_criteria_migration
    config_path, settings_path = map(Path, sys.argv[2:4])
    settings = json.loads(settings_path.read_text())
    config = json.loads(config_path.read_text())
    root = (settings_path.parent / settings["root"]).resolve()
    project_lock = (root / settings["lock"]).resolve()
    task_lock = (config_path.parent / config["paths"]["state"] / config["paths"]["lock"]).resolve()
    actual = locking.exclusive_lock
    events, held = [], set()

    @contextmanager
    def tracked(path, seconds, poll):
        selected = Path(path).resolve()
        assert selected not in held, "Recursive acquisition of a held migration lock."
        assert selected != project_lock, "Migration acquired the shared project lock."
        with actual(path, seconds, poll):
            held.add(selected)
            events.append(str(selected))
            try:
                yield
            finally:
                held.remove(selected)

    out, err = io.StringIO(), io.StringIO()
    with ExitStack() as patches, redirect_stdout(out), redirect_stderr(err):
        for module in (locking, project_config, stage_criteria_migration):
            patches.enter_context(patch.object(module, "exclusive_lock", tracked))
        patches.enter_context(patch.object(sys, "argv", ["poise", "task-workflow-migrate",
                              "--config", str(config_path), "--project-settings", str(settings_path)]))
        try:
            code = cli()
        except SystemExit as failure:
            code = failure.code
    assert not held
    print(json.dumps({"code": code, "result": json.loads(out.getvalue()) if out.getvalue().strip() else {},
                      "stderr": err.getvalue(), "events": events}))


def main():
    tools = WorkTools(Poise(Path(sys.argv[1]), "project-migration-observer", FixtureClock()))
    response = tools.invoke({
        "operation": "show", "messages": [],
        "input": {"queries": [{"id": "read", "kind": "work_overview",
                              "sprint_statuses": None, "standalone_task_statuses": None}]},
    })
    print(json.dumps(response["results"][0]["value"], ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1] == "migration-locks":
        migration_lock_probe()
    else:
        main()
