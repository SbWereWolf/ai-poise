from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def policy_rules_present() -> bool:
    development = (ROOT / "docs/governance/development-rules.md").read_text()
    planning = (ROOT / "docs/workflows/task-planning.md").read_text()
    batch = (ROOT / "docs/workflows/batch-work.md").read_text()
    agents = (ROOT / "AGENTS.md").read_text()
    return all(
        (
            "Rework не снимает неизменность" in development,
            "Некорректный граф этапов" in development,
            "неизменяемый" in planning and "allowed_paths" in planning,
            "до начала rework" in batch and "allowed_paths" in batch,
            "registered immutable artifacts remain immutable" in agents,
            "Rework never lifts path immutability" in agents,
            "invalid stage graphs" in agents,
        )
    )


def test_rework_and_task_creation_path_rules_are_canonical() -> None:
    assert policy_rules_present()
