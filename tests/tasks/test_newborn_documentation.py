"""Documentation contract for the newborn Task lifecycle."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_newborn_documentation_contract():
    batch = (ROOT / "docs/workflows/batch-work.md").read_text(encoding="utf-8")
    sprints = (ROOT / "docs/workflows/sprints.md").read_text(encoding="utf-8")
    skill = (ROOT / ".agents/skills/poise/SKILL.md").read_text(encoding="utf-8")
    source_rules = (ROOT / "src/AGENTS.md").read_text(encoding="utf-8")

    assert "operation: task" in batch
    assert "newborn" in batch
    assert "materialize_tasks" in sprints
    assert "real newborn Task" in skill
    assert "newborn Task" in source_rules
