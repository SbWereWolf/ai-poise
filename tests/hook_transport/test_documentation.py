import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_native_source_resolution_is_canonical_and_cross_referenced():
    runtime = (ROOT / "docs/configuration/runtime-hooks.md").read_text(encoding="utf-8")
    boundaries = (ROOT / "docs/architecture/boundaries.md").read_text(encoding="utf-8")
    local = (ROOT / "docs/configuration/project-setup.md").read_text(encoding="utf-8")

    assert "Разрешение source root для native launcher" in runtime
    for term in (
        "codex-hook-main",
        "native binding",
        "installation source",
        "Task worktree",
        "session-scoped",
            "не использует fallback",
        "config_hash",
    ):
        assert term in runtime
    for term in (
        "session-scoped source resolution",
        "WorkTools",
        "native binding",
        "не запускает дочерний `WorkTools`",
        "Task worktree",
    ):
        assert term in boundaries
    assert "worktree/src" in local and "installation source" in local
    assert "не исполняется из собственного" in local

    for path in (
        ROOT / "docs/configuration/runtime-hooks.md",
        ROOT / "docs/architecture/boundaries.md",
        ROOT / "docs/configuration/project-setup.md",
    ):
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            relative = target.split("#", 1)[0]
            assert (path.parent / relative).resolve().exists(), (path, target)
