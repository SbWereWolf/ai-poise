from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/migrations/erp-runtime-migration-retrospective-2026-09-15.md"
ANCHOR = "#разрешение-путей-и-рабочие-копии"
FORBIDDEN = (
    "session root",
    "worktree root",
    "working root",
    "source root",
    "main root",
    "сессионный корень",
    "корень рабочей копии",
)


def test_path_semantics_have_one_exact_documentation_anchor():
    text = DOC.read_text()
    assert "## Разрешение путей и рабочие копии" in text
    assert "filesystem path" in text
    assert "`cwd`" in text
    lowered = text.lower()
    assert not any(term in lowered for term in FORBIDDEN)


def test_agent_rules_link_to_the_exact_path_semantics_heading():
    root_rules = (ROOT / "AGENTS.md").read_text()
    source_rules = (ROOT / "src/AGENTS.md").read_text()
    assert f"erp-runtime-migration-retrospective-2026-09-15.md{ANCHOR}" in root_rules
    assert f"erp-runtime-migration-retrospective-2026-09-15.md{ANCHOR}" in source_rules
    combined = (root_rules + "\n" + source_rules).lower()
    assert "explicit filesystem path" in combined
    assert not any(term in combined for term in FORBIDDEN)
