"""A03: file formatting is a general service, independent of project identity.

Subject: poise.infrastructure.markdown_format.format_files, real filesystem.
All expected bytes are independent literals. No specialised package catalogue is
created. The healthy call in every case is necessary to expose early rejection.
"""
import pytest

from poise.common import PoiseError
from poise.infrastructure.markdown_format import format_files
from tests.hook_transport.architecture_support import trace


SOURCE = b"one two three four\r\n"
EXPECTED = b"one two\nthree\nfour\n"


def project_directory(parent, name, *, manifest=True):
    root = parent / name
    root.mkdir()
    if manifest:
        (root / "pyproject.toml").write_text(
            '[project]\nname="' + name + '"\n', encoding="utf-8"
        )
    document = root / "document.md"
    document.write_bytes(SOURCE)
    document.chmod(0o640)
    return root, document


def admitted(root, selected, mode, marker):
    trace(marker, "format_files_enter", checkout=str(root), files=[str(p) for p in selected], mode=mode)
    try:
        result = format_files(root, [str(p) for p in selected], 9, "LF", mode,
                              "No applicable IDE formatter in this test")
    except PoiseError as exc:
        trace(marker, "format_files_refusal", reason=str(exc))
        raise AssertionError(
            f"{marker}: actual general formatter rejected: {exc}"
        ) from exc
    assert result["mode"] == mode, marker
    assert result["files"] == [str(p) for p in selected], marker
    trace(marker, "format_files_return", result=result)
    return result


def test_different_project_names_share_check_and_fix(tmp_path):
    marker = "ARCH_FIX_GENERIC_FORMATTER"
    for name in ("audit-alpha", "audit-beta"):
        root, document = project_directory(tmp_path, name)
        before = document.stat()
        checked = admitted(root, [document], "check", marker)
        assert checked["status"] == "drift", marker
        assert checked["drift"] == [str(document)], marker
        assert checked["changed"] == [], marker
        assert document.read_bytes() == SOURCE, marker
        assert document.stat().st_mtime_ns == before.st_mtime_ns, marker
        fixed = admitted(root, [document], "fix", marker)
        assert fixed["status"] == "ok", marker
        assert fixed["changed"] == [str(document)], marker
        assert document.read_bytes() == EXPECTED, marker
        assert document.stat().st_mode & 0o777 == 0o640, marker
        after = document.stat()
        again = admitted(root, [document], "fix", marker)
        assert again["changed"] == [], marker
        assert document.stat().st_mtime_ns == after.st_mtime_ns, marker
        assert document.stat().st_ino == after.st_ino, marker


def test_rename_does_not_change_result(tmp_path):
    marker = "ARCH_FIX_FORMATTER_NAME"
    root, document = project_directory(tmp_path, "audit-before")
    before = admitted(root, [document], "check", marker)
    (root / "pyproject.toml").write_text(
        '[project]\nname="audit-after"\n', encoding="utf-8"
    )
    after = admitted(root, [document], "check", marker)
    assert after == before, marker
    fixed = admitted(root, [document], "fix", marker)
    assert fixed["status"] == "ok", marker
    assert document.read_bytes() == EXPECTED, marker


def test_only_selected_files_change_in_generic_project(tmp_path):
    marker = "ARCH_FIX_FORMATTER_SELECTION"
    # A valid explicit directory does not need Git, pyproject or a test registry.
    root, document = project_directory(tmp_path, "ordinary-directory", manifest=False)
    second = root / "second.md"
    second.write_bytes(SOURCE)
    second.chmod(0o600)
    sentinel = root / "unselected.md"
    sentinel.write_bytes(b"unselected  bytes\r\n")
    sentinel.chmod(0o640)
    before = sentinel.stat()
    result = admitted(root, [document, second], "fix", marker)
    assert result["changed"] == [str(document), str(second)], marker
    assert document.read_bytes() == second.read_bytes() == EXPECTED, marker
    assert document.stat().st_mode & 0o777 == 0o640, marker
    assert second.stat().st_mode & 0o777 == 0o600, marker
    assert sentinel.read_bytes() == b"unselected  bytes\r\n", marker
    assert sentinel.stat().st_mtime_ns == before.st_mtime_ns, marker
    assert sentinel.stat().st_mode == before.st_mode, marker


def test_late_invalid_file_keeps_generic_batch_unchanged(tmp_path):
    marker = "ARCH_FIX_FORMATTER_ATOMICITY"
    root, document = project_directory(tmp_path, "ordinary-atomic")
    # Rejecting a project name must not make the negative control vacuously pass.
    assert admitted(root, [document], "check", marker)["status"] == "drift", marker
    before = document.stat()
    with pytest.raises(PoiseError):
        format_files(root, [str(document), str(root / "missing.md")],
                     9, "LF", "fix", "No applicable IDE formatter in this test")
    assert document.read_bytes() == SOURCE, marker
    assert document.stat().st_mtime_ns == before.st_mtime_ns, marker
    assert document.stat().st_mode == before.st_mode, marker
    assert not (root / "missing.md").exists(), marker
