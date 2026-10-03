"""Integration publishes product files while retaining site-owned configuration."""

from pathlib import Path
import stat

from conftest import WorkPoise as Poise, git
from poise.application.work import WorkTools

from .helpers import integration_input, prepare_completed_task, request


def test_integration_preserves_target_site_config_and_blocks_ignored_child_loss(project):
    target_site = project["app"] / "src" / "site.settings.json"
    target_site.write_bytes(b'{"site":"target-private"}\n')
    target_site.chmod(0o600)
    exclude = project["app"] / ".git" / "info" / "exclude"
    exclude.write_text(exclude.read_text(encoding="utf-8") + "\nsrc/site.settings.json\n", encoding="utf-8")
    assert git(project["app"], "status", "--porcelain", "--untracked-files=all") == ""

    def publish_example(root):
        (root / "src" / ".gitignore").write_text("site.settings.json\n", encoding="utf-8")
        (root / "src" / "site.settings.example.json").write_bytes(b'{"site":"example"}\n')

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    worktree = Path(worktree)
    child_site = worktree / "src" / "site.settings.json"
    child_site.write_bytes(b'{"site":"child-private"}\n')
    child_site.chmod(0o600)
    target_before = target_site.read_bytes()
    target_mode = stat.S_IMODE(target_site.stat().st_mode)
    packet = request("integrate", integration_input(project, accepted))

    blocked = tools.invoke(packet)

    assert blocked["status"] == "cleanup_pending"
    assert blocked["cleanup"]["task_worktree"] == "blocked"
    assert target_site.read_bytes() == target_before == b'{"site":"target-private"}\n'
    assert stat.S_IMODE(target_site.stat().st_mode) == target_mode == 0o600
    assert child_site.read_bytes() == b'{"site":"child-private"}\n'
    assert worktree.is_dir()
    assert (project["app"] / "src" / "site.settings.example.json").read_bytes() == b'{"site":"example"}\n'
    assert git(project["app"], "ls-files", "src/site.settings.json") == ""

    child_site.unlink()
    resumed = WorkTools(Poise(project["config_path"], "ignored-cleanup-resumer")).invoke(packet)
    assert resumed["status"] == "integrated"
    assert target_site.read_bytes() == target_before
    assert not worktree.exists()
