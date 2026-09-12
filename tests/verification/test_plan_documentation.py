"""Canonical and agent-facing verification-plan documentation parity."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_canonical_workflow_documents_plan_matrix_and_early_validation():
    governance = text("docs/governance/development-rules.md")
    additions = text("docs/workflows/content-requirements.md")
    evidence = text("docs/workflows/evidence.md")

    for token in (
        "verification_plan",
        "responsibility",
        "change_surface",
        "red_stages",
        "green_stages",
        "red_failure",
        "allowed_paths",
    ):
        assert token in governance
    assert "method_additions" in additions and "verification_plan" in additions
    assert "до" in additions and "побоч" in additions
    assert "stdout_equals" in evidence and "stderr_equals" in evidence
    assert "дополнитель" in evidence and "RED" in evidence


def test_architecture_documents_owner_restore_and_no_source_inference():
    boundaries = text("docs/architecture/boundaries.md")
    library = text("docs/architecture/library-api.md")
    status = text("docs/architecture/implementation-status.md")

    for document in (boundaries, library, status):
        assert "verification_plan" in document
        assert "CheckRegistry" in document
    assert "allowed_paths" in boundaries
    assert "migration" in library or "миграц" in library
    assert "pytest" in status and "рекурс" in status


def test_english_agent_rules_match_the_canonical_contract():
    root_rules = text("AGENTS.md")
    source_rules = text("src/AGENTS.md")

    for document in (root_rules, source_rules):
        for token in (
            "verification_plan",
            "change_surface",
            "red_stages",
            "green_stages",
            "allowed_paths",
        ):
            assert token in document
        assert "additional" in document and "RED" in document


def test_task_0050_surface_example_is_preserved_in_canonical_documentation():
    governance = text("docs/governance/development-rules.md")

    assert "docs/**" in governance
    assert "AGENTS.md" in governance
    assert ".agents/**" in governance
    assert "allowed_paths" in governance
