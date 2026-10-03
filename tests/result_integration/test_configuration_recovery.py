"""Real-Git recovery of explicitly identified local configuration."""

import hashlib
import os
from pathlib import Path
import stat

import pytest

from conftest import WorkPoise as Poise, git
from poise.application.work import WorkTools
from poise.infrastructure.result_integration import RuntimeResultIntegration
from poise.infrastructure.task_cleanup import RuntimeTaskResourceCleanup
from poise.modules.foundation.errors import PoiseError

from .helpers import integration_input, prepare_completed_task, request


def _recovery_event(result):
    events = [
        item for item in result["history"]
        if item.get("event") == "configuration_backup_completed"
    ]
    assert len(events) == 1
    return events[0]["details"]


def _produced_recovery_files(project):
    root = project["root"] / "state" / "standalone" / "T1" / "configuration-recovery"
    return sorted(path for path in root.rglob("*") if path.is_file())


def test_verified_code_publishes_and_declared_child_config_is_backed_up(project):
    target = project["app"] / "src" / "site.settings.json"
    target.write_bytes(b'{"site":"old-product-value"}\n')
    git(project["app"], "add", "src/site.settings.json")
    git(project["app"], "commit", "-m", "Track initial settings")

    def publish_example(root):
        git(root, "mv", "src/site.settings.json", "src/site.settings.example.json")
        (root / "src" / ".gitignore").write_text("site.settings.json\n", encoding="utf-8")

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    worktree = Path(worktree)
    child = worktree / "src" / "site.settings.json"
    private_bytes = b'{"site":"child-private-value"}\n'
    child.write_bytes(private_bytes)
    child.chmod(0o640)
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/site.settings.json"]

    result = tools.invoke(request("integrate", packet))

    assert result["status"] == "integrated"
    assert result["publication"]["status"] == "confirmed"
    assert not target.exists()  # Git publication may remove the tracked old path.
    assert (project["app"] / "src" / "site.settings.example.json").exists()
    assert not worktree.exists()
    receipt = _recovery_event(result)
    assert len(receipt["files"]) == 1
    saved = receipt["files"][0]
    assert saved["path"] == "src/site.settings.json"
    assert saved["status"] == "copied"
    assert saved["sha256"] == hashlib.sha256(private_bytes).hexdigest()
    assert saved["source_mode"] == 0o640
    backup = Path(saved["recovery_path"])
    assert backup.is_relative_to(project["root"] / "state" / "standalone" / "T1")
    assert backup.read_bytes() == private_bytes
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    recovery_root = project["root"] / "state" / "standalone" / "T1" / "configuration-recovery"
    directory = backup.parent
    while True:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        if directory == recovery_root:
            break
        directory = directory.parent
    assert "child-private-value" not in str(result)

    replay = tools.invoke(request("integrate", packet))
    assert replay["status"] == "integrated"
    assert replay["replayed"] is True
    assert _recovery_event(replay) == receipt
    assert backup.read_bytes() == private_bytes


def test_only_declared_config_is_backed_up(project):
    def publish_example(root):
        (root / "src" / ".gitignore").write_text(
            "site.settings.json\nlocal.cache\n", encoding="utf-8"
        )

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    worktree = Path(worktree)
    (worktree / "src" / "site.settings.json").write_bytes(b"declared private bytes\n")
    (worktree / "src" / "local.cache").write_bytes(b"undeclared cache\n")
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/site.settings.json"]

    result = tools.invoke(request("integrate", packet))

    assert result["status"] == "integrated"
    assert not worktree.exists()
    files = _recovery_event(result)["files"]
    assert [item["path"] for item in files] == ["src/site.settings.json"]
    assert Path(files[0]["recovery_path"]).read_bytes() == b"declared private bytes\n"


def test_two_distinct_declared_configs_have_independent_copies_and_receipts(project):
    def publish_example(root):
        (root / "src" / ".gitignore").write_text(
            "first.settings.json\nsecond.settings.json\nlocal.cache\n", encoding="utf-8"
        )

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    child = Path(worktree) / "src"
    first = child / "first.settings.json"
    second = child / "second.settings.json"
    first.write_bytes(b"first private value\n")
    first.chmod(0o640)
    second.write_bytes(b"different second value\n")
    second.chmod(0o600)
    (child / "local.cache").write_bytes(b"undeclared cache\n")
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/first.settings.json", "src/second.settings.json"]

    result = tools.invoke(request("integrate", packet))

    assert result["status"] == "integrated"
    assert result["publication"]["status"] == "confirmed"
    assert not Path(worktree).exists()
    files = _recovery_event(result)["files"]
    assert [item["path"] for item in files] == packet["config_backup_paths"]
    for item, expected_bytes, source_mode in zip(
        files, (b"first private value\n", b"different second value\n"),
        (0o640, 0o600), strict=True,
    ):
        saved = Path(item["recovery_path"])
        assert saved.read_bytes() == expected_bytes
        assert item["sha256"] == hashlib.sha256(expected_bytes).hexdigest()
        assert item["source_mode"] == source_mode
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert _produced_recovery_files(project) == sorted(
        Path(item["recovery_path"]) for item in files
    )


def test_later_declared_failure_keeps_earlier_copy_for_cold_exact_replay(project):
    def publish_example(root):
        (root / "src" / ".gitignore").write_text(
            "first.settings.json\nsecond.settings.json\nlocal.cache\n", encoding="utf-8"
        )

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    child = Path(worktree) / "src"
    first = child / "first.settings.json"
    first.write_bytes(b"first private value\n")
    second = child / "second.settings.json"
    outside = project["root"] / "outside-secret.json"
    outside.write_bytes(b"outside private value\n")
    second.symlink_to(outside)
    (child / "local.cache").write_bytes(b"undeclared cache\n")
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/first.settings.json", "src/second.settings.json"]

    blocked = tools.invoke(request("integrate", packet))

    assert blocked["status"] == "cleanup_pending"
    assert blocked["publication"]["status"] == "confirmed"
    assert Path(worktree).exists()
    assert first.read_bytes() == b"first private value\n"
    assert second.is_symlink()
    assert not any(item.get("event") == "configuration_backup_completed"
                   for item in blocked["history"])
    [saved_first] = _produced_recovery_files(project)
    assert saved_first.name == "first.settings.json"
    assert saved_first.read_bytes() == b"first private value\n"
    first_inode = saved_first.stat().st_ino

    second.unlink()
    second.write_bytes(b"second private value\n")
    resumed = WorkTools(Poise(project["config_path"], "multi-config-resumer")).invoke(
        request("integrate", packet)
    )
    assert resumed["status"] == "integrated"
    assert saved_first.stat().st_ino == first_inode
    assert saved_first.read_bytes() == b"first private value\n"
    files = _recovery_event(resumed)["files"]
    assert [item["path"] for item in files] == packet["config_backup_paths"]
    assert Path(files[1]["recovery_path"]).read_bytes() == b"second private value\n"
    assert _produced_recovery_files(project) == sorted(
        Path(item["recovery_path"]) for item in files
    )


def test_target_local_config_is_preserved_while_child_config_is_backed_up(project):
    target = project["app"] / "src" / "site.settings.json"
    target.write_bytes(b"target operator settings\n")

    def publish_example(root):
        (root / "src" / ".gitignore").write_text("site.settings.json\n", encoding="utf-8")

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    child = Path(worktree) / "src" / "site.settings.json"
    child.write_bytes(b"child operator settings\n")
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/site.settings.json"]

    result = tools.invoke(request("integrate", packet))

    assert result["status"] == "integrated"
    assert target.read_bytes() == b"target operator settings\n"
    saved = _recovery_event(result)["files"][0]
    assert Path(saved["recovery_path"]).read_bytes() == b"child operator settings\n"


def test_absent_declared_config_is_reported_without_blocking_publication(project):
    tools, worktree, accepted = prepare_completed_task(
        project,
        lambda root: (root / "src" / "feature.py").write_text("VALUE = 1\n"),
    )
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/not-created.settings.json"]

    result = tools.invoke(request("integrate", packet))

    assert result["status"] == "integrated"
    assert result["publication"]["status"] == "confirmed"
    assert not Path(worktree).exists()
    assert _recovery_event(result)["files"] == [{
        "path": "src/not-created.settings.json", "status": "absent"
    }]


def test_unsafe_declared_symlink_blocks_cleanup_after_publication_and_replays(project):
    def publish_example(root):
        (root / "src" / ".gitignore").write_text("site.settings.json\n", encoding="utf-8")

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    worktree = Path(worktree)
    outside = project["root"] / "operator-secret.json"
    outside.write_bytes(b"outside private bytes\n")
    link = worktree / "src" / "site.settings.json"
    link.symlink_to(outside)
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/site.settings.json"]

    blocked = tools.invoke(request("integrate", packet))

    assert blocked["status"] == "cleanup_pending"
    assert blocked["publication"]["status"] == "confirmed"
    assert git(project["app"], "rev-parse", "refs/heads/main") == blocked["target_after"]
    assert worktree.exists() and link.is_symlink()
    assert outside.read_bytes() == b"outside private bytes\n"
    assert "outside private bytes" not in str(blocked)

    link.unlink()
    link.write_bytes(b"safe private bytes\n")
    resumed = WorkTools(Poise(project["config_path"], "config-backup-resumer")).invoke(
        request("integrate", packet)
    )
    assert resumed["status"] == "integrated"
    assert not worktree.exists()
    saved = _recovery_event(resumed)["files"][0]
    assert Path(saved["recovery_path"]).read_bytes() == b"safe private bytes\n"


def test_unreadable_declared_config_preserves_worktree_after_publication(project):
    def publish_example(root):
        (root / "src" / ".gitignore").write_text("site.settings.json\n", encoding="utf-8")

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    child = Path(worktree) / "src" / "site.settings.json"
    child.write_bytes(b"private settings\n")
    child.chmod(0)
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/site.settings.json"]

    blocked = tools.invoke(request("integrate", packet))

    assert blocked["status"] == "cleanup_pending"
    assert blocked["publication"]["status"] == "confirmed"
    assert Path(worktree).exists()
    assert blocked["history"][-1]["details"]["receipt"] == {
        "reason": "configuration_backup_failed"
    }
    child.chmod(0o600)
    resumed = tools.invoke(request("integrate", packet))
    assert resumed["status"] == "integrated"


def test_changed_source_or_backup_blocks_cleanup_on_replay(project, monkeypatch):
    def publish_example(root):
        (root / "src" / ".gitignore").write_text("site.settings.json\n", encoding="utf-8")

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    child = Path(worktree) / "src" / "site.settings.json"
    child.write_bytes(b"original settings\n")
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/site.settings.json"]
    original_remove = RuntimeTaskResourceCleanup.remove

    def fail_worktree_removal(self, task_id, resource):
        if resource.kind == "worktree":
            raise PoiseError("simulated cleanup interruption")
        return original_remove(self, task_id, resource)

    with monkeypatch.context() as patch:
        patch.setattr(RuntimeTaskResourceCleanup, "remove", fail_worktree_removal)
        interrupted = tools.invoke(request("integrate", packet))
    assert interrupted["status"] == "cleanup_pending"
    saved = _recovery_event(interrupted)["files"][0]
    backup = Path(saved["recovery_path"])
    assert backup.read_bytes() == b"original settings\n"

    child.write_bytes(b"changed settings\n")
    changed = tools.invoke(request("integrate", packet))
    assert changed["status"] == "cleanup_pending"
    assert Path(worktree).exists()
    assert backup.read_bytes() == b"original settings\n"

    child.write_bytes(b"original settings\n")
    backup.write_bytes(b"corrupt backup\n")
    corrupt = tools.invoke(request("integrate", packet))
    assert corrupt["status"] == "cleanup_pending"
    assert Path(worktree).exists()
    assert backup.read_bytes() == b"corrupt backup\n"

    backup.write_bytes(b"original settings\n")
    completed = tools.invoke(request("integrate", packet))
    assert completed["status"] == "integrated"
    assert not Path(worktree).exists()


def test_copy_before_receipt_interruption_reuses_real_copy_on_cold_replay(
    project, monkeypatch
):
    def publish_example(root):
        (root / "src" / ".gitignore").write_text("site.settings.json\n", encoding="utf-8")

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    child = Path(worktree) / "src" / "site.settings.json"
    child.write_bytes(b"private settings\n")
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/site.settings.json"]
    original_save = RuntimeResultIntegration._save
    interrupted = False

    def interrupt_after_copy(self, task_id, run, expected_version):
        nonlocal interrupted
        if (not interrupted and run.history[-1].get("event")
                == "configuration_backup_completed"):
            interrupted = True
            raise RuntimeError("simulated interruption before backup receipt persistence")
        return original_save(self, task_id, run, expected_version)

    with monkeypatch.context() as patch:
        patch.setattr(RuntimeResultIntegration, "_save", interrupt_after_copy)
        with pytest.raises(RuntimeError, match="before backup receipt persistence"):
            tools.invoke(request("integrate", packet))

    assert interrupted
    assert Path(worktree).exists()
    [saved] = _produced_recovery_files(project)
    assert saved.read_bytes() == b"private settings\n"
    inode = saved.stat().st_ino
    persisted = tools.invoke(request("show", {"queries": [{
        "id": "saved", "kind": "integration", "task_id": "T1",
        "request_id": packet["request_id"],
    }]}))["results"][0]["value"]
    assert persisted["status"] == "cleanup_pending"
    assert persisted["publication"]["status"] == "confirmed"
    assert not any(item.get("event") == "configuration_backup_completed"
                   for item in persisted["history"])

    result = WorkTools(Poise(project["config_path"], "copy-interruption-resumer")).invoke(
        request("integrate", packet)
    )

    assert result["status"] == "integrated"
    assert not Path(worktree).exists()
    assert saved.stat().st_ino == inode
    assert saved.read_bytes() == b"private settings\n"
    assert _recovery_event(result)["files"][0]["recovery_path"] == str(saved)
    assert _produced_recovery_files(project) == [saved]


def test_partial_copy_failure_keeps_source_and_never_claims_a_backup(
    project, monkeypatch
):
    def publish_example(root):
        (root / "src" / ".gitignore").write_text("site.settings.json\n", encoding="utf-8")

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    child = Path(worktree) / "src" / "site.settings.json"
    child.write_bytes(b"private settings requiring a complete copy\n")
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/site.settings.json"]
    original_fdopen = os.fdopen
    interrupted = False

    class PartialWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, kind, error, traceback):
            return self.stream.__exit__(kind, error, traceback)

        def write(self, content):
            nonlocal interrupted
            self.stream.write(content[:7])
            self.stream.flush()
            os.fsync(self.stream.fileno())
            interrupted = True
            raise OSError("simulated partial recovery write")

    def partial_recovery_fdopen(fd, mode="r", *args, **kwargs):
        stream = original_fdopen(fd, mode, *args, **kwargs)
        if mode != "wb":
            return stream
        try:
            path = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            return stream
        return PartialWriter(stream) if "configuration-recovery" in path else stream

    with monkeypatch.context() as patch:
        patch.setattr(os, "fdopen", partial_recovery_fdopen)
        blocked = tools.invoke(request("integrate", packet))

    assert interrupted
    assert blocked["status"] == "cleanup_pending"
    assert blocked["publication"]["status"] == "confirmed"
    assert Path(worktree).exists()
    assert child.read_bytes() == b"private settings requiring a complete copy\n"
    assert not any(item.get("event") == "configuration_backup_completed"
                   for item in blocked["history"])
    [partial] = _produced_recovery_files(project)
    assert partial.read_bytes() == b"private"
    assert "private settings requiring a complete copy" not in str(blocked)

    still_blocked = WorkTools(Poise(project["config_path"], "partial-copy-resumer")).invoke(
        request("integrate", packet)
    )
    assert still_blocked["status"] == "cleanup_pending"
    assert partial.read_bytes() == b"private"
    assert child.read_bytes() == b"private settings requiring a complete copy\n"
    partial.unlink()
    completed = tools.invoke(request("integrate", packet))
    assert completed["status"] == "integrated"
    saved = Path(_recovery_event(completed)["files"][0]["recovery_path"])
    assert saved.read_bytes() == b"private settings requiring a complete copy\n"


@pytest.mark.parametrize("declaration", ["omitted", []])
def test_absent_or_empty_backup_list_keeps_the_existing_integration_contract(
    project, declaration
):
    tools, worktree, accepted = prepare_completed_task(
        project, lambda root: (root / "src" / "feature.py").write_text("VALUE = 1\n")
    )
    packet = integration_input(project, accepted)
    if declaration != "omitted":
        packet["config_backup_paths"] = declaration

    result = tools.invoke(request("integrate", packet))

    assert result["status"] == "integrated"
    assert result["publication"]["status"] == "confirmed"
    assert not Path(worktree).exists()
    assert not any(
        item.get("event") == "configuration_backup_completed"
        for item in result["history"]
    )


def test_changed_backup_list_is_rejected_on_exact_request_replay(project):
    def publish_example(root):
        (root / "src" / ".gitignore").write_text("site.settings.json\n", encoding="utf-8")

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    (Path(worktree) / "src" / "site.settings.json").write_bytes(b"private settings\n")
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/site.settings.json"]
    result = tools.invoke(request("integrate", packet))
    backup = Path(_recovery_event(result)["files"][0]["recovery_path"])
    changed = {**packet, "config_backup_paths": ["src/other.settings.json"]}

    with pytest.raises(PoiseError, match="immutable"):
        tools.invoke(request("integrate", changed))

    assert git(project["app"], "rev-parse", "refs/heads/main") == result["target_after"]
    assert backup.read_bytes() == b"private settings\n"


def test_declared_directory_blocks_cleanup_after_code_publication(project):
    def publish_example(root):
        (root / "src" / ".gitignore").write_text("local.settings/\n", encoding="utf-8")

    tools, worktree, accepted = prepare_completed_task(project, publish_example)
    child = Path(worktree) / "src" / "local.settings"
    child.mkdir()
    (child / "private.json").write_bytes(b"private settings\n")
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = ["src/local.settings"]

    result = tools.invoke(request("integrate", packet))

    assert result["status"] == "cleanup_pending"
    assert result["publication"]["status"] == "confirmed"
    assert child.is_dir()
    assert (child / "private.json").read_bytes() == b"private settings\n"
    assert "private settings" not in str(result)


@pytest.mark.parametrize("bad_paths", [
    None,
    "src/site.settings.json",
    [42],
    [""],
    ["../outside.json"],
    ["/absolute.json"],
    ["src/site.settings.json", "src/site.settings.json"],
    ["src\\site.settings.json"],
    ["src/./site.settings.json"],
    ["src/bad\x00settings.json"],
])
def test_invalid_backup_declaration_rejects_before_publication(project, bad_paths):
    tools, worktree, accepted = prepare_completed_task(
        project, lambda root: (root / "src" / "feature.py").write_text("VALUE = 1\n")
    )
    before = git(project["app"], "rev-parse", "refs/heads/main")
    packet = integration_input(project, accepted)
    packet["config_backup_paths"] = bad_paths

    with pytest.raises(PoiseError, match="config_backup_paths"):
        tools.invoke(request("integrate", packet))

    assert git(project["app"], "rev-parse", "refs/heads/main") == before
    assert Path(worktree).exists()
    with pytest.raises(PoiseError, match="not found"):
        tools.invoke(request("show", {"queries": [{
            "id": "saved", "kind": "integration", "task_id": "T1",
            "request_id": packet["request_id"],
        }]}))
    assert _produced_recovery_files(project) == []
