import hashlib
import importlib.util

import pytest

from conftest import WorkPoise as Poise
from poise.modules.foundation.errors import PoiseError


def cleanup_types():
    package_spec = importlib.util.find_spec("poise.modules.task_cleanup")
    assert package_spec is not None, "cleanup domain is not implemented"
    domain_spec = importlib.util.find_spec("poise.modules.task_cleanup.domain")
    adapter_spec = importlib.util.find_spec("poise.infrastructure.task_cleanup")
    assert domain_spec is not None and adapter_spec is not None, "cleanup infrastructure is not implemented"
    from poise.infrastructure.task_cleanup import RuntimeTaskResourceCleanup
    from poise.modules.task_cleanup.domain import TaskOwnedResource
    return RuntimeTaskResourceCleanup, TaskOwnedResource


def descriptor(resource_type, path, content):
    return {
        "kind": resource_type,
        "path": str(path),
        "digest": hashlib.sha256(content).hexdigest(),
    }


def test_exact_registered_temporary_files_and_backups_are_removed(project):
    adapter_type, resource_type = cleanup_types()
    runtime = Poise(project["config_path"], "cleanup-adapter")
    owned = runtime.runtime / "task-cleanup" / "T1"
    owned.mkdir(parents=True)
    temporary = owned / "merge.index"
    backup = owned / "recovery.bundle.tmp"
    temporary_content = b"temporary index\n"
    backup_content = b"temporary backup\n"
    temporary.write_bytes(temporary_content)
    backup.write_bytes(backup_content)
    operator_backup = runtime.state / "backups" / "operator.sqlite"
    operator_backup.parent.mkdir(parents=True, exist_ok=True)
    operator_backup.write_bytes(b"operator backup\n")
    adapter = adapter_type(runtime)

    temporary_receipt = adapter.remove(
        "T1", resource_type.parse(descriptor("temporary", temporary, temporary_content))
    )
    backup_receipt = adapter.remove(
        "T1", resource_type.parse(descriptor("temporary_backup", backup, backup_content))
    )

    assert temporary_receipt["status"] == "removed"
    assert backup_receipt["status"] == "removed"
    assert not temporary.exists() and not backup.exists()
    assert operator_backup.read_bytes() == b"operator backup\n"


def test_temporary_cleanup_rejects_digest_drift_and_foreign_paths(project):
    adapter_type, resource_type = cleanup_types()
    runtime = Poise(project["config_path"], "cleanup-adapter")
    owned = runtime.runtime / "task-cleanup" / "T1"
    owned.mkdir(parents=True)
    changed = owned / "changed.tmp"
    changed.write_bytes(b"changed content\n")
    foreign = project["root"] / "foreign.tmp"
    foreign.write_bytes(b"foreign content\n")
    adapter = adapter_type(runtime)

    with pytest.raises(PoiseError, match="digest|changed"):
        adapter.remove(
            "T1", resource_type.parse(descriptor("temporary", changed, b"original content\n"))
        )
    with pytest.raises(PoiseError, match="owner|owned|runtime|path"):
        adapter.remove(
            "T1", resource_type.parse(descriptor("temporary", foreign, b"foreign content\n"))
        )

    assert changed.read_bytes() == b"changed content\n"
    assert foreign.read_bytes() == b"foreign content\n"
