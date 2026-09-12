from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_terminal_task_cleanup_is_documented_for_native_runtime():
    runtime = (ROOT / "docs/configuration/runtime-hooks.md").read_text(encoding="utf-8")

    for term in (
        "cancel_tasks",
        "cancel",
        "конкурент",
    ):
        assert term in runtime
