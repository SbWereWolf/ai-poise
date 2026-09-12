from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_migration_guide_names_public_commands_and_quiescence_requirement():
    guide = (ROOT / "docs/route-count-limit-migration.md").read_text(encoding="utf-8")

    assert "poise backup create" in guide
    assert "poise route-migrate" in guide
    assert "остановите агентов и процессы" in guide
    assert "task_workflows" in guide
    assert "идемпотент" in guide
