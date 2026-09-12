import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def source(path):
    target = ROOT / path
    assert target.is_file(), f"missing planned cleanup owner: {path}"
    return target.read_text(encoding="utf-8")


def parsed(path):
    return ast.parse(source(path))


def test_one_cleanup_owner_is_shared_by_terminal_and_integration_paths():
    domain = source("src/poise/modules/task_cleanup/domain.py")
    application = parsed("src/poise/application/task_cleanup.py")
    infrastructure = parsed("src/poise/infrastructure/task_cleanup.py")
    integration = parsed("src/poise/infrastructure/result_integration.py")
    work = parsed("src/poise/application/work.py")

    assert "subprocess" not in domain and "sqlite" not in domain.lower()
    assert any(
        isinstance(node, ast.ClassDef) and node.name == "TaskResourceCleanup"
        for node in ast.walk(application)
    )
    assert any(
        isinstance(node, ast.ClassDef) and node.name == "RuntimeTaskResourceCleanup"
        for node in ast.walk(infrastructure)
    )
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "task_cleanup"
        and any(alias.name == "RuntimeTaskResourceCleanup" for alias in node.names)
        for node in ast.walk(integration)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RuntimeTaskResourceCleanup"
        for node in ast.walk(integration)
    )
    assert any(
        isinstance(node, ast.Compare)
        and any(isinstance(value, ast.Constant) and value.value == "cleanup" for value in node.comparators)
        for node in ast.walk(work)
    )


def test_cleanup_implementation_contains_no_force_or_transport_owned_deletion():
    infrastructure = source("src/poise/infrastructure/task_cleanup.py")
    transport = source("src/poise/application/work.py")
    forbidden = ("--force", "branch\", \"-D", "reset\", \"--hard", "git clean", "git push")

    assert not any(token in infrastructure for token in forbidden)
    assert "worktree\", \"remove" not in transport
    assert "UPDATE tasks" not in transport
