from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_stale_observation_replacement_contract_is_documented_for_agents():
    documentation = (ROOT / "docs/workflows/evidence.md").read_text(encoding="utf-8")
    workflow_skill = (ROOT / ".agents/skills/poise/SKILL.md").read_text(
        encoding="utf-8"
    )
    development_skill = (
        ROOT / ".agents/skills/poise-development/SKILL.md"
    ).read_text(encoding="utf-8")
    agent_rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    anchor = "../../../docs/workflows/evidence.md#замена-устаревшего-метода-наблюдения"

    assert "### Замена устаревшего метода наблюдения" in documentation
    for term in (
        "evidence_plan.subject_methods",
        "executable_obligations",
        "evidence_kind",
        "covers",
        "verification_registry_changed",
        "replayed=true",
        "kind=verification_registry",
    ):
        assert term in documentation
    assert anchor in workflow_skill
    assert "one guarded `method_additions` change" in workflow_skill
    assert "do not recreate the Task or edit its\ndatabase" in workflow_skill
    assert anchor in development_skill
    assert "Structural registry edits remain owned by `test_registry`" in development_skill
    assert (
        "docs/workflows/evidence.md#замена-устаревшего-метода-наблюдения"
        in agent_rules
    )
