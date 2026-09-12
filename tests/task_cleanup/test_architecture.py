from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def source(path):
    target = ROOT / path
    assert target.is_file(), f"missing planned cleanup owner: {path}"
    return target.read_text(encoding="utf-8")


def test_one_cleanup_owner_is_shared_by_terminal_and_integration_paths():
    domain = source("src/poise/modules/task_cleanup/domain.py")
    application = source("src/poise/application/task_cleanup.py")
    infrastructure = source("src/poise/infrastructure/task_cleanup.py")
    integration = source("src/poise/infrastructure/result_integration.py")
    work = source("src/poise/application/work.py")

    assert "subprocess" not in domain and "sqlite" not in domain.lower()
    assert "TaskResourceCleanup" in application
    assert "RuntimeTaskResourceCleanup" in infrastructure
    assert "RuntimeTaskResourceCleanup" in integration
    assert "elif op=='cleanup'" in work or 'elif op == "cleanup"' in work


def test_cleanup_implementation_contains_no_force_or_transport_owned_deletion():
    infrastructure = source("src/poise/infrastructure/task_cleanup.py")
    transport = source("src/poise/application/work.py")
    forbidden = ("--force", "branch\", \"-D", "reset\", \"--hard", "git clean", "git push")

    assert not any(token in infrastructure for token in forbidden)
    assert "worktree\", \"remove" not in transport
    assert "UPDATE tasks" not in transport
