from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_docs_define_exact_status_and_reconcile_contracts():
    goal_config = (ROOT / "docs/configuration/goal-config.md").read_text(encoding="utf-8")
    library = (ROOT / "docs/architecture/library-api.md").read_text(encoding="utf-8")

    assert "goal-config-status-1" in goal_config
    assert "goal-config-reconcile-1" in goal_config
    assert "новую БД редактора" in goal_config
    assert "GoalConfigCommands.reconcile" in library


def test_operational_skills_require_public_reconcile_instead_of_a_fresh_store():
    for relative in (
        ".agents/skills/poise/SKILL.md",
        ".agents/skills/poise-development/SKILL.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "goal-config-reconcile-1" in text
        assert "Do not create a fresh editor database" in text

