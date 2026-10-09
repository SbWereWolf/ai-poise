"""Real filesystem effects and fresh-runtime recovery (R4,R5,R6)."""
from contextlib import contextmanager
from pathlib import Path
import os

import pytest

from batch.helpers import request
from poise.common import PoiseError
from .helpers import (absent, agree, arrange,
                      declaration, delivery_status, file_output, finish, fresh, settle)


@pytest.mark.parametrize("phase", ["before-unlink", "after-proof-unlink", "after-root-removal"])
def test_deletion_failure_is_truthful_and_resume_finishes_without_redelivery(project, monkeypatch, phase):
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    fired = False
    if phase in ("before-unlink", "after-proof-unlink"):
        original = os.unlink
        def injected(path, *args, **kwargs):
            nonlocal fired
            if not fired and str(path).endswith("proof.txt"):
                fired = True
                if phase == "after-proof-unlink":
                    original(path, *args, **kwargs)
                raise OSError("INJECTED-OWNED-UNLINK-FAILURE")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(os, "unlink", injected)
    else:
        original = os.rmdir
        def injected(path, *args, **kwargs):
            nonlocal fired
            effect = original(path, *args, **kwargs)
            if not fired and Path(path) == root:
                fired = True
                raise OSError("INJECTED-AFTER-ROOT-REMOVAL")
            return effect
        monkeypatch.setattr(os, "rmdir", injected)
    assert_first_failure(lambda: finish(project, tools, commit, "cancelled"))
    state = delivery_status(fresh(project))
    assert state["status"] in ("delivery_pending", "delivery_blocked", "delivery_partial")
    assert fired, "The test must reach the actual deletion effect"
    assert destination.read_bytes() == b"customer result\n"
    if phase == "after-root-removal":
        absent(root)
    else:
        assert root.is_dir()
        assert (root / "artifacts" / "proof.txt").exists() == (phase == "before-unlink")
    inode = destination.stat().st_ino
    monkeypatch.undo()
    result = settle(fresh(project))
    assert result["status"] == "delivery_complete"
    assert destination.stat().st_ino == inode
    assert destination.read_bytes() == b"customer result\n"
    absent(root)


def test_after_delivery_effect_before_receipt_retry_observes_existing_bytes(project, monkeypatch):
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    fired = False
    def publication_fault(original):
        def injected(src, dst, *args, **kwargs):
            nonlocal fired
            effect = original(src, dst, *args, **kwargs)
            if not fired and Path(dst) == destination:
                fired = True
                raise OSError("INJECTED-AFTER-DELIVERY-EFFECT")
            return effect
        return injected
    # Existing publishers use link or atomic rename. Observe the filesystem
    # effect without prescribing which non-overwriting publisher owns it.
    for operation in ("replace", "rename", "link"):
        monkeypatch.setattr(os, operation, publication_fault(getattr(os, operation)))
    assert_first_failure(lambda: finish(project, tools, commit, "cancelled"))
    state = delivery_status(fresh(project))
    assert state["status"] in ("delivery_pending", "delivery_blocked", "delivery_partial")
    assert fired, "The test must reach actual atomic destination publication"
    assert root.is_dir() and source.read_bytes() == b"customer result\n"
    assert destination.read_bytes() == b"customer result\n"
    inode = destination.stat().st_ino
    monkeypatch.undo()
    result = settle(fresh(project))
    assert result["status"] == "delivery_complete"
    assert destination.stat().st_ino == inode
    assert destination.read_bytes() == b"customer result\n"
    absent(root)


def test_database_failure_after_removal_is_reconciled_by_new_runtime(project, monkeypatch):
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    original = tools.runtime.store.unit_of_work
    fired = False
    @contextmanager
    def injected():
        nonlocal fired
        with original() as uow:
            yield uow
            if not fired and not os.path.lexists(root):
                fired = True
                raise OSError("INJECTED-BEFORE-SUCCESS-COMMIT")
    monkeypatch.setattr(tools.runtime.store, "unit_of_work", injected)
    assert_first_failure(lambda: finish(project, tools, commit, "cancelled"))
    state = delivery_status(fresh(project))
    assert state["status"] in ("delivery_pending", "delivery_blocked", "delivery_partial")
    assert fired, "The test must reach the post-removal persistence transaction"
    absent(root)
    assert destination.read_bytes() == b"customer result\n"
    monkeypatch.undo()
    result = settle(fresh(project))
    assert result["status"] == "delivery_complete"
    absent(root)
    assert destination.read_bytes() == b"customer result\n"


@pytest.mark.parametrize("link", ["root", "parent", "nested", "dangling"])
def test_links_never_allow_deletion_of_foreign_material(project, link):
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    foreign = project["root"] / "foreign-data"
    foreign.mkdir()
    sentinel = foreign / "keep.txt"
    sentinel.write_bytes(b"foreign sentinel\n")
    moved = root.with_name("original-T1")
    if link == "root":
        root.rename(moved)
        root.symlink_to(foreign, target_is_directory=True)
    elif link == "parent":
        moved = root.parent.with_name("original-parent")
        root.parent.rename(moved)
        root.parent.symlink_to(foreign, target_is_directory=True)
    else:
        (root / "foreign-link").symlink_to(foreign if link == "nested" else foreign / "missing")
    try:
        finish(project, tools, commit, "cancelled")
    except PoiseError as error:
        assert any(word in str(error).lower() for word in ("link", "owner", "path", "root"))
    assert sentinel.read_bytes() == b"foreign sentinel\n"
    assert foreign.is_dir()
    if link in ("root", "parent"):
        assert moved.is_dir(), "Rejected ownership substitution must retain original inputs"


def test_missing_source_before_retirement_is_integrity_failure_not_retired_evidence(project):
    tools, root, source, commit = arrange(project)
    agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    source.unlink()
    finish(project, tools, commit, "cancelled")
    result = settle(fresh(project))
    assert result["status"] == "delivery_blocked"
    assert result["reason"] == "source_integrity_failed"
    assert root.is_dir()


def assert_first_failure(action):
    try:
        first = action()
    except (OSError, PoiseError) as error:
        assert "INJECTED" in str(error)
    else:
        assert first["status"] in ("cleanup_blocked", "delivery_blocked", "delivery_partial", "delivery_pending")
    return None


def test_failure_before_publication_is_persisted_without_losing_any_source(project, monkeypatch):
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    fired = []
    def before_effect(original):
        def invoke(src, dst, *args, **kwargs):
            if Path(dst) == destination:
                fired.append(str(dst))
                raise OSError("INJECTED-BEFORE-PUBLICATION")
            return original(src, dst, *args, **kwargs)
        return invoke
    for operation in ("replace", "rename", "link"):
        monkeypatch.setattr(os, operation, before_effect(getattr(os, operation)))
    assert_first_failure(lambda: finish(project, tools, commit, "cancelled"))
    assert fired and not destination.exists()
    assert root.is_dir() and source.read_bytes() == b"customer result\n"
    state = delivery_status(fresh(project))
    assert state["status"] in ("delivery_pending", "delivery_blocked", "delivery_partial")
    monkeypatch.undo()
    assert settle(fresh(project))["status"] == "delivery_complete"
    assert destination.read_bytes() == b"customer result\n"
    absent(root)


def test_customer_effect_without_ack_remains_unknown_then_ack_continues_without_resend(project):
    import json
    tools, root, source, commit = arrange(project)
    received = project["root"] / "received.txt"
    acknowledgement = project["root"] / "ack.json"
    output = file_output(source, received, "customer")
    output["acknowledgement"] = {"path": str(acknowledgement), "receiver": "fixture-customer"}
    agree(tools, declaration([output]))
    # The independent receiver commits its external effect, then its process
    # stops before acknowledgement. No product coordinator/receipt is mocked.
    received.write_bytes(b"customer result\n")
    inode = received.stat().st_ino
    finish(project, tools, commit, "cancelled")
    state = delivery_status(fresh(project))
    assert state["status"] == "delivery_blocked"
    assert state["reason"] == "customer_acknowledgement_required"
    assert not acknowledgement.exists()
    assert root.is_dir() and received.stat().st_ino == inode
    receipt = json.loads((Path(__file__).parent / "fixtures" / "protocol.json").read_text())["customer_acknowledgement"]
    receipt["received_path"] = str(received)
    acknowledgement.write_text(json.dumps(receipt))
    ack_bytes = acknowledgement.read_bytes()
    assert settle(fresh(project))["status"] == "delivery_complete"
    assert received.stat().st_ino == inode and received.read_bytes() == b"customer result\n"
    assert acknowledgement.read_bytes() == ack_bytes
    absent(root)


def test_customer_acknowledged_effect_survives_failure_before_local_receipt(project, monkeypatch):
    import json
    tools, root, source, commit = arrange(project)
    received = project["root"] / "received.txt"
    acknowledgement = project["root"] / "ack.json"
    received.write_bytes(b"customer result\n")
    inode = received.stat().st_ino
    receipt = json.loads((Path(__file__).parent / "fixtures" / "protocol.json").read_text())["customer_acknowledgement"]
    receipt["received_path"] = str(received)
    acknowledgement.write_text(json.dumps(receipt))
    ack_bytes = acknowledgement.read_bytes()
    output = file_output(source, received, "customer")
    output["acknowledgement"] = {"path": str(acknowledgement), "receiver": "fixture-customer"}
    agree(tools, declaration([output]))
    observed_ack = []
    original_open = Path.open
    original_os_open = os.open
    def observed_file(path, *args, **kwargs):
        if Path(path) == acknowledgement:
            observed_ack.append(str(path))
        return original_open(path, *args, **kwargs)
    def observed_fd(path, *args, **kwargs):
        if isinstance(path, (str, bytes, Path)) and Path(path) == acknowledgement:
            observed_ack.append(str(path))
        return original_os_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed_file)
    monkeypatch.setattr(os, "open", observed_fd)
    transaction = tools.runtime.store.database.transaction
    fired = []
    @contextmanager
    def interrupted_commit():
        with transaction() as database:
            yield database
            if observed_ack and not fired:
                fired.append(True)
                raise OSError("INJECTED-ACK-BEFORE-LOCAL-RECEIPT")
    monkeypatch.setattr(tools.runtime.store.database, "transaction", interrupted_commit)
    assert_first_failure(lambda: finish(project, tools, commit, "cancelled"))
    assert observed_ack and fired
    state = delivery_status(fresh(project))
    assert state["status"] in ("delivery_pending", "delivery_partial", "delivery_blocked")
    assert received.stat().st_ino == inode and received.read_bytes() == b"customer result\n"
    assert acknowledgement.read_bytes() == ack_bytes
    monkeypatch.undo()
    assert settle(fresh(project))["status"] == "delivery_complete"
    assert received.stat().st_ino == inode and acknowledgement.read_bytes() == ack_bytes
    absent(root)


@pytest.mark.parametrize("damage", [
    "file-missing", "file-changed", "git-target", "configuration-not-ignored",
    "customer-ack-missing", "customer-bytes-changed",
])
def test_partial_retirement_rechecks_each_permanent_confirmation_without_source(project, monkeypatch, damage):
    """001: an unfinished removal cannot consume a stale permanent confirmation."""
    import json
    from conftest import git

    tools, root, source, commit = arrange(project)
    stable = project["root"] / "stable-result.txt"
    destination = project["root"] / "second-result.txt"
    outputs = [file_output(source, stable)]
    output = file_output(source, destination)
    output["id"] = "second-output"
    acknowledgement = project["root"] / "customer-ack.json"
    exclude = Path(git(project["app"], "rev-parse", "--absolute-git-dir")) / "info" / "exclude"
    original_exclude = exclude.read_bytes()
    if damage == "git-target":
        git(project["app"], "merge", "--ff-only", commit)
        prior_target = git(project["app"], "rev-parse", commit + "^")
        output = {"id": "second-output", "kind": "git", "repository": str(project["app"]),
                  "target_ref": "refs/heads/main", "commit": commit}
    elif damage == "configuration-not-ignored":
        destination = project["app"] / "local" / "operator.env"
        exclude.write_bytes(original_exclude + b"\n/local/\n")
        output.update(kind="ignored_configuration", destination=str(destination))
    elif damage.startswith("customer-"):
        destination.write_bytes(b"customer result\n")
        ack = json.loads((Path(__file__).parent / "fixtures" / "protocol.json").read_text())["customer_acknowledgement"]
        ack["received_path"] = str(destination)
        acknowledgement.write_text(json.dumps(ack))
        original_ack = acknowledgement.read_bytes()
        output.update(kind="customer", acknowledgement={"path": str(acknowledgement), "receiver": "fixture-customer"})
    outputs.append(output)
    agree(tools, declaration(outputs))
    foreign = root.parent / "other-task-sentinel.txt"
    foreign.write_bytes(b"other task remains\n")
    original_unlink = os.unlink
    fired = []
    with monkeypatch.context() as fault:
        def interrupted(path, *args, **kwargs):
            if not fired and str(path).endswith("proof.txt"):
                fired.append(True)
                raise OSError("INJECTED-PARTIAL-RETIREMENT")
            return original_unlink(path, *args, **kwargs)
        fault.setattr(os, "unlink", interrupted)
        with pytest.raises(PoiseError, match="INJECTED-PARTIAL-RETIREMENT"):
            finish(project, tools, commit, "cancelled")
    assert fired and root.is_dir()
    assert delivery_status(fresh(project))["status"] == "delivery_partial"
    assert stable.read_bytes() == b"customer result\n"
    if damage != "git-target":
        assert destination.read_bytes() == b"customer result\n"
    # The original source may legitimately already be gone after partial rmtree.
    source.unlink(missing_ok=True)
    assert not os.path.lexists(source)
    remaining = _remaining_material(root)
    stable_inode = stable.stat().st_ino
    if damage == "file-missing":
        destination.unlink()
        reason = "destination_unavailable"
    elif damage in ("file-changed", "customer-bytes-changed"):
        destination.write_bytes(b"externally changed permanent bytes\n")
        reason = "destination_conflict" if damage == "file-changed" else "customer_acknowledgement_invalid"
    elif damage == "git-target":
        git(project["app"], "update-ref", "refs/heads/main", prior_target)
        reason = "integration_required"
    elif damage == "configuration-not-ignored":
        exclude.write_bytes(original_exclude)
        reason = "destination_unavailable"
    else:
        acknowledgement.unlink()
        reason = "customer_acknowledgement_required"
    damaged_bytes = destination.read_bytes() if destination.is_file() else None

    blocked = settle(fresh(project))

    assert blocked["status"] == "delivery_blocked", "001: stale confirmation permitted remaining deletion"
    assert blocked["reason"] == reason
    assert delivery_status(fresh(project))["status"] == "delivery_blocked"
    assert root.is_dir() and _remaining_material(root) == remaining
    assert not os.path.lexists(source)
    assert stable.read_bytes() == b"customer result\n" and stable.stat().st_ino == stable_inode
    assert foreign.read_bytes() == b"other task remains\n"
    if damaged_bytes is None:
        assert not os.path.lexists(destination)
    else:
        assert destination.read_bytes() == damaged_bytes
    if damage == "git-target":
        assert git(project["app"], "rev-parse", "main") == prior_target
        git(project["app"], "update-ref", "refs/heads/main", commit)
    elif damage == "configuration-not-ignored":
        assert exclude.read_bytes() == original_exclude
        exclude.write_bytes(original_exclude + b"\n/local/\n")
    elif damage == "customer-ack-missing":
        assert not acknowledgement.exists()
        acknowledgement.write_bytes(original_ack)
    else:
        # Explicit operator restoration, never automatic repair/overwrite by settle.
        destination.write_bytes(b"customer result\n")
    restored_inode = destination.stat().st_ino if destination.is_file() else None
    result = settle(fresh(project))
    assert result["status"] == "delivery_complete"
    absent(root)
    assert not os.path.lexists(source)
    assert stable.read_bytes() == b"customer result\n" and stable.stat().st_ino == stable_inode
    assert foreign.read_bytes() == b"other task remains\n"
    if damage == "git-target":
        assert git(project["app"], "merge-base", commit, "main") == commit
    else:
        assert destination.read_bytes() == b"customer result\n"
        assert destination.stat().st_ino == restored_inode
    if damage.startswith("customer-"):
        assert acknowledgement.read_bytes() == original_ack


def _remaining_material(root):
    """Independent byte inventory; receipt state is not the deletion oracle."""
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
            for path in root.rglob("*")}
