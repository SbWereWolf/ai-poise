from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "docs/governance/development-rules.md"
SPRINTS = ROOT / "docs/workflows/sprints.md"
RETRO = ROOT / "docs/migrations/erp-runtime-migration-retrospective-2026-09-15.md"


def test_worktree_placement_is_owned_by_the_target_codebase():
    rules = RULES.read_text()
    assert "### Размещение task worktree" in rules
    section = rules.split("### Размещение task worktree", 1)[1].split("## ", 1)[0]
    assert "кодовой базы, которую изменяет Task" in section
    assert "не хранится внутри mutable state AI poise" in section
    assert "без отдельного worktree" in section
    assert "настроенного repository checkout" in section


def test_sprint_policy_requires_dependency_closed_local_graphs_and_conditional_duplicates():
    text = SPRINTS.read_text()
    assert "### Замкнутость зависимостей и локальные дубли" in text
    section = text.split("### Замкнутость зависимостей и локальные дубли", 1)[1].split("## ", 1)[0]
    section=" ".join(section.split())
    assert "остаётся standalone" in section
    assert "локальная conditional-duplicate Task с уникальным ID" in section
    assert "Дубль нужен для локального графа" in section
    assert "не для параллельной повторной реализации" in section
    assert "Между Sprint dependency edge не создаётся" in section


def test_agent_projection_links_to_exact_worktree_and_sprint_policy_headings():
    agents = (ROOT / "AGENTS.md").read_text()
    assert "development-rules.md#размещение-task-worktree" in agents
    assert "sprints.md#замкнутость-зависимостей-и-локальные-дубли" in agents


def test_migration_retrospective_records_dependency_closed_sprints_without_fixed_size_goal():
    text = RETRO.read_text()
    assert "## Правило состава Sprint" in text
    assert "размер Sprint не является целью" in text
    assert "C027.1, C027.2 и C027.3" in text
    assert "standalone" in text
    assert "C026.1 **исключён" in text
    assert "семь задач" not in text
