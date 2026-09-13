"""Documentation contract for the newborn Task lifecycle."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_newborn_documentation_contract():
    batch = (ROOT / "docs/workflows/batch-work.md").read_text(encoding="utf-8")
    sprints = (ROOT / "docs/workflows/sprints.md").read_text(encoding="utf-8")
    governance = (ROOT / "docs/governance/development-rules.md").read_text(
        encoding="utf-8"
    )
    requirements = json.loads(
        (ROOT / "docs/spec-data/requirements.json").read_text(encoding="utf-8")
    )
    skill = (ROOT / ".agents/skills/poise/SKILL.md").read_text(encoding="utf-8")
    source_rules = (ROOT / "src/AGENTS.md").read_text(encoding="utf-8")

    assert "## Жизненный цикл newborn Task" in batch
    assert "operation: task" in batch
    for action in ("create", "edit", "ready"):
        assert f"`{action}`" in batch
    assert "общий ownership API" in batch
    assert "до выбора `goal_type` маршрут отсутствует" in batch
    assert "прямая полная creation" in batch

    assert "## Реальные newborn Task в Sprint draft" in sprints
    assert "materialize_tasks" in sprints
    assert "legacy embedded" in sprints
    assert "частич" in sprints
    assert "истори" in sprints
    assert "До публикации задачи отсутствуют" not in sprints

    assert "newborn Task" in governance
    assert "0072" in governance and "0073" in governance
    texts = [item["text"] for item in requirements["requirements"]]
    newborn_rule = next(text for text in texts if "newborn Task" in text)
    for term in ("available", "goal_type", "Sprint", "ownership"):
        assert term in newborn_rule

    for english in (skill, source_rules):
        assert "real newborn Task" in english
        assert "shared ownership API" in english
        assert "no route entry before goal_type selection" in english
        assert "materialize_tasks" in english
