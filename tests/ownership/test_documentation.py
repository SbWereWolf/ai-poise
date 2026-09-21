from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def section(path, heading):
    body = text(path)
    start = body.index(heading)
    tail = body[start + len(heading):]
    level = len(heading) - len(heading.lstrip("#"))
    next_heading = re.search(rf"\n#{{1,{level}}} ", tail)
    if next_heading is None:
        return body[start:]
    return body[start:start + len(heading) + next_heading.start()]


def test_canonical_russian_documents_define_atomic_session_ownership():
    rules = section("docs/governance/development-rules.md", "## Владение Task и worktree").lower()
    requirements = section("docs/governance/requirements.md", "### HR-037. Владение Task и worktree").lower()
    boundaries = section("docs/architecture/boundaries.md", "## WorkOwnership").lower()
    happy_path = section("docs/workflows/happy-path.md", "## Владение рабочим набором").lower()
    handoff = section("docs/workflows/local-handoff.md", "## Передача между исполнителем и ревьюером").lower()

    for phrase in ("четыре комбинации", "одной task", "одним worktree", "неопределён"):
        assert phrase in rules
    for phrase in ("зависимый worktree", "не изменяет wip", "живой владелец"):
        assert phrase in requirements
    for phrase in ("tasks.claimed_by", "sessions.task_id", "одной транзакц"):
        assert phrase in boundaries
    assert "автоматически замен" in happy_path
    assert "зависимый worktree" in handoff
    assert "terminal cleanup" in boundaries and "result integration" in boundaries


def test_english_agent_projection_links_the_canonical_rule_and_handoff_policy():
    canonical_link = "docs/governance/development-rules.md#владение-task-и-worktree"
    root_rules = text("AGENTS.md")
    source_rules = text("src/AGENTS.md")
    skill = text(".agents/skills/poise/SKILL.md")

    for body in (root_rules, source_rules, skill):
        assert "one Task and one worktree" in body
        assert "uncertain live claim" in body
        assert "dependent worktree" in body
        assert canonical_link in body
    assert "directly messages the known counterpart" in root_rules
    assert "directly messages the known counterpart" in skill


def test_crash_recovery_documents_operator_decision_not_fabricated_liveness():
    body = text('docs/workflows/crash-ownership-recovery.md')
    for term in ('after_crash', 'ownership_recovery', 'recovery_template',
                 'writers_stopped', 'expected_snapshot', 'SessionEnd', 'restart'):
        assert term in body
    for path in ('AGENTS.md', 'src/AGENTS.md', '.agents/skills/poise/SKILL.md',
                 '.agents/skills/debugging-and-recovery/SKILL.md',
                 'docs/workflows/checkpoint-recovery.md'):
        assert 'crash-ownership-recovery.md' in text(path)
