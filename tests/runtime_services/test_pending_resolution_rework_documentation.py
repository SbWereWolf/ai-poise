from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_pending_resolution_rework_policy_is_documented_for_humans_and_agents():
    workflow = (ROOT / "docs/workflows/batch-work.md").read_text(encoding="utf-8")
    governance = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    workflow_skill = (ROOT / ".agents/skills/poise/SKILL.md").read_text(encoding="utf-8")
    development_skill = (
        ROOT / ".agents/skills/poise-development/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "нерассмотренн" in workflow.lower()
    assert "pending resolution" in governance.lower()
    assert "pending resolution" in workflow_skill.lower()
    assert "pending resolution" in development_skill.lower()
    assert "recover_empty_rework" in workflow
