from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_documents_define_explicit_task_process_migration():
    requirements = (ROOT / "docs/governance/requirements.md").read_text(encoding="utf-8").lower()
    migration = (ROOT / "docs/task-process-snapshot-migration.md").read_text(encoding="utf-8").lower()
    backups = (ROOT / "docs/task-db-backups.md").read_text(encoding="utf-8").lower()
    boundaries = (ROOT / "docs/architecture/boundaries.md").read_text(encoding="utf-8").lower()
    for phrase in ("task-process-migrate", "request_id", "backup_name", "одной транзакц"):
        assert phrase in migration
    for phrase in ("worktree_required", "явная миграция", "без fallback"):
        assert phrase in requirements
    assert "task process snapshot migration" in boundaries
    assert "task-process-migrate" in backups


def test_source_agent_rule_links_migration_guide_and_forbids_direct_repair():
    source_rules = (ROOT / "src/AGENTS.md").read_text(encoding="utf-8")
    assert "docs/task-process-snapshot-migration.md" in source_rules
    assert "Do not repair stored process snapshots through direct SQL" in source_rules
