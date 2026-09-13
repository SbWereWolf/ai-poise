from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_workflow_documents_automatic_snapshot_index_isolation():
    workflow = (ROOT / "docs/workflows/batch-work.md").read_text(encoding="utf-8")

    assert "временный Git index каждого snapshot-вызова изолирован" in workflow
    assert "не удаляет `snapshot.index.lock` вручную" in workflow


def test_both_operational_skills_prohibit_manual_snapshot_lock_deletion():
    for relative in (
        ".agents/skills/poise/SKILL.md",
        ".agents/skills/poise-development/SKILL.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "Never delete `snapshot.index.lock` manually" in text

