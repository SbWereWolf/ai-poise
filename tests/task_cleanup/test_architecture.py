import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def source(path):
    target = ROOT / path
    assert target.is_file(), f"missing planned cleanup owner: {path}"
    return target.read_text(encoding="utf-8")


def parsed(path):
    return ast.parse(source(path))


def dotted_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = dotted_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return ""


def routes_operation_to(tree, operation, owner_call):
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        compared = [node.test.left, *node.test.comparators]
        if not any(isinstance(value, ast.Constant) and value.value == operation for value in compared):
            continue
        if any(
            isinstance(candidate, ast.Call) and dotted_name(candidate.func) == owner_call
            for statement in node.body
            for candidate in ast.walk(statement)
        ):
            return True
    return False


def assigns_constructed_owner(tree, target, constructor):
    return any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(dotted_name(candidate) == target for candidate in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        ))
        and isinstance(node.value, ast.Call)
        and dotted_name(node.value.func).endswith(constructor)
        for node in ast.walk(tree)
    )


def test_one_cleanup_owner_is_shared_by_terminal_and_integration_paths():
    domain = source("src/poise/modules/task_cleanup/domain.py")
    application = parsed("src/poise/application/task_cleanup.py")
    infrastructure = parsed("src/poise/infrastructure/task_cleanup.py")
    integration = parsed("src/poise/infrastructure/result_integration.py")
    runtime = parsed("src/poise/runtime.py")
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
    assert assigns_constructed_owner(runtime, "self.cleanup_tools", "TaskResourceCleanup")
    assert routes_operation_to(work, "cleanup", "h.cleanup_tools.apply")


def test_cleanup_implementation_contains_no_force_or_transport_owned_deletion():
    infrastructure = source("src/poise/infrastructure/task_cleanup.py")
    transport = source("src/poise/application/work.py")
    forbidden = ("--force", "branch\", \"-D", "reset\", \"--hard", "git clean", "git push")

    assert not any(token in infrastructure for token in forbidden)
    assert "worktree\", \"remove" not in transport
    assert "UPDATE tasks" not in transport
