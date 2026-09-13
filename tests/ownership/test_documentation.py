from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_canonical_russian_documents_define_atomic_session_ownership():
    combined = "\n".join(
        text(path)
        for path in (
            "docs/governance/development-rules.md",
            "docs/governance/requirements.md",
            "docs/architecture/boundaries.md",
            "docs/workflows/happy-path.md",
            "docs/workflows/local-handoff.md",
        )
    ).lower()

    for phrase in (
        "четыре комбинации",
        "одной task",
        "одним worktree",
        "неопределён",
        "зависимый worktree",
        "не изменяет wip",
    ):
        assert phrase in combined


def test_english_agent_projection_links_the_canonical_rule_and_handoff_policy():
    combined = "\n".join(
        text(path)
        for path in (
            "AGENTS.md",
            "src/AGENTS.md",
            ".agents/skills/poise/SKILL.md",
        )
    )

    for phrase in (
        "one Task and one worktree",
        "uncertain live claim",
        "dependent worktree",
        "directly messages the known counterpart",
        "docs/governance/development-rules.md",
    ):
        assert phrase in combined

