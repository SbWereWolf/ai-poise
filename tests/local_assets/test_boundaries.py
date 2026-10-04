"""Real local-assets boundaries; expectations and effects are test-owned."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_cli import ERRORS, case, tree_state


ENTRIES = [("first.conf", b"first supplied", "tracked"),
           ("nested/second.conf", b"second supplied", "working")]


def expected_reply(status: str, target: Path, project: str, paths: list[str]) -> dict:
    fixture = Path(__file__).parent / "fixtures" / f"boundary-{status}-reply.json"
    expected = json.loads(fixture.read_text())
    expected["target_root"], expected["project"] = str(target), project
    for item, path in zip(expected["assets"], paths):
        item["path"] = path
    return expected


def process_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    # The fixture chooses its Git inputs explicitly; the subject receives them intact.
    return {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            **(extra or {})}


def git(root: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         "-c", "commit.gpgsign=false", "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid", "-C", str(root), *args],
        env=process_environment(), capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr


def arrange(tmp_path: Path) -> tuple[Path, Path, Path, dict, Path]:
    target, source, manifest, request = case(tmp_path, ENTRIES)
    outside = tmp_path / "outside"
    outside.mkdir()
    for root in (target, source, outside):
        (root / "sentinel.txt").write_bytes(b"preserve fixture sentinel")
    return target, source, manifest, request, outside


def invoke(manifest: Path, request: dict, extra: dict[str, str] | None = None):
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "poise", "local-assets", "--manifest", str(manifest)],
        input=json.dumps(request), env=process_environment(extra),
        text=True, capture_output=True, check=False,
    )
    try:
        reply = json.loads(completed.stdout)
    except json.JSONDecodeError:
        reply = None
    return completed, reply


def states(roots: dict[str, Path]) -> dict:
    return {name: tree_state(root) for name, root in roots.items()}


def observe(node, label: str, completed, reply, roots: dict[str, Path], before: dict) -> None:
    target = roots["target"]
    after = states(roots)
    node.user_properties.append(("boundary_observation", {
        "case": label, "exit_code": completed.returncode,
        "selected_target_root": str(target),
        "stdout": completed.stdout, "stderr": completed.stderr, "reply": reply,
        "changed": {name: after[name] != before[name] for name in roots},
        "first_published": (target / "first.conf").is_file()
        and (target / "first.conf").read_bytes() == b"first supplied",
        "second_published": (target / "nested/second.conf").is_file()
        and (target / "nested/second.conf").read_bytes() == b"second supplied",
        "later_parent_created": (target / "new-parent").is_dir(),
        "selected_git_present": (target / ".git").exists(),
    }))


def rejected(completed, reply, code: str) -> None:
    assert completed.returncode == ERRORS[code]["exit_code"], completed.stderr + completed.stdout
    assert completed.stderr == ""
    assert reply == {**ERRORS[code]["reply"], "asset": None}


REQUEST_CASES = [
    ("project", 0xD800),
    ("project", 0xDBFF),
    ("project", 0xDC00),
    ("project", 0xDFFF),
    ("target_root", 0xD800),
    ("source_root", 0xD800),
]
REQUEST_IDS = ["project-high-start", "project-high-end", "project-low-start",
               "project-low-end", "target_root", "source_root"]


@pytest.mark.parametrize(("field", "codepoint"), REQUEST_CASES, ids=REQUEST_IDS)
def test_unpaired_surrogate_request_is_rejected_before_effects(
    tmp_path: Path, request: pytest.FixtureRequest, field: str, codepoint: int,
) -> None:
    target, source, manifest, packet, outside = arrange(tmp_path)
    packet[field] += chr(codepoint)
    if field == "project":
        declaration = json.loads(manifest.read_text())
        declaration["project"] = packet[field]
        manifest.write_text(json.dumps(declaration), encoding="utf-8")
    roots = {"target": target, "source": source, "outside": outside}
    before = states(roots)
    manifest_before = manifest.read_bytes(), manifest.stat().st_mtime_ns
    completed, reply = invoke(manifest, packet)
    observe(request.node, request.node.callspec.id, completed, reply, roots, before)
    rejected(completed, reply, "invalid_request")
    assert states(roots) == before
    assert (manifest.read_bytes(), manifest.stat().st_mtime_ns) == manifest_before


@pytest.mark.parametrize("field", ["project", "target_root", "later_path",
                                   "later_source_path", "working_repair"])
def test_unpaired_surrogate_declaration_is_rejected_before_effects(
    tmp_path: Path, request: pytest.FixtureRequest, field: str,
) -> None:
    target, source, manifest, packet, outside = arrange(tmp_path)
    declaration = json.loads(manifest.read_text())
    if field in ("project", "target_root"):
        declaration[field] += "\ud800"
    elif field == "later_path":
        declaration["assets"][1]["path"] = "new-parent/\ud800.conf"
    elif field == "later_source_path":
        declaration["assets"][1]["source_path"] = "bad-\ud800/second.conf"
    else:
        declaration["assets"][1]["repair"] = "owner repair \ud800"
    manifest.write_text(json.dumps(declaration), encoding="utf-8")
    roots = {"target": target, "source": source, "outside": outside}
    before = states(roots)
    manifest_before = manifest.read_bytes(), manifest.stat().st_mtime_ns
    completed, reply = invoke(manifest, packet)
    observe(request.node, field, completed, reply, roots, before)
    rejected(completed, reply, "invalid_manifest")
    assert states(roots) == before
    assert (manifest.read_bytes(), manifest.stat().st_mtime_ns) == manifest_before


def donor(tmp_path: Path) -> Path:
    root = tmp_path / "donor"
    root.mkdir()
    git(root, "init", "-q")
    (root / "sentinel.txt").write_bytes(b"donor must remain unchanged")
    return root


def change_target(manifest: Path, packet: dict, target: Path) -> None:
    declaration = json.loads(manifest.read_text())
    declaration["target_root"] = packet["target_root"] = str(target)
    manifest.write_text(json.dumps(declaration), encoding="utf-8")


def test_donor_git_environment_does_not_validate_plain_target(
    tmp_path: Path, request: pytest.FixtureRequest,
) -> None:
    _, source, manifest, packet, outside = arrange(tmp_path)
    target = tmp_path / "plain"
    target.mkdir()
    (target / "sentinel.txt").write_bytes(b"plain target untouched")
    foreign = donor(tmp_path)
    change_target(manifest, packet, target)
    roots = {"target": target, "source": source, "outside": outside, "donor": foreign}
    before = states(roots)
    control, control_reply = invoke(manifest, packet)
    rejected(control, control_reply, "invalid_target_root")
    assert states(roots) == before
    completed, reply = invoke(manifest, packet, {
        "GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(target),
    })
    observe(request.node, "plain-donor", completed, reply, roots, before)
    rejected(completed, reply, "invalid_target_root")
    assert states(roots) == before
    assert not (target / ".git").exists()


def selected_target(tmp_path: Path, target: Path, kind: str) -> Path:
    if kind == "ordinary":
        return target
    (target / "README").write_bytes(b"linked worktree fixture\n")
    git(target, "add", "README")
    git(target, "commit", "-q", "-m", "Create linked fixture")
    linked = tmp_path / "linked"
    git(target, "worktree", "add", "-q", "-b", "fixture-linked", str(linked))
    assert (linked / ".git").is_file()
    return linked


def success_and_repeat(target, source, manifest, packet, outside, *, primary=None):
    declaration = json.loads(manifest.read_text())
    paths = [item["path"] for item in declaration["assets"]]
    roots = {"target": target, "source": source, "outside": outside}
    if primary is not None:
        roots["primary"] = primary
    before = states(roots)
    first, reply = invoke(manifest, packet)
    assert first.returncode == 0, first.stderr + first.stdout
    assert first.stderr == ""
    assert reply == expected_reply("restored", target, packet["project"], paths)
    for item, (_, expected, _) in zip(declaration["assets"], ENTRIES):
        destination = target / item["path"]
        assert destination.read_bytes() == expected
        assert destination.stat().st_mode & 0o777 == 0o600
    after = states(roots)
    for name in roots.keys() - {"target"}:
        assert after[name] == before[name]
    for path, value in before["target"].items():
        assert after["target"][path] == value
    added = set(paths)
    for path in paths:
        added.update(str(parent) for parent in Path(path).parents if str(parent) != ".")
    assert set(after["target"]) - set(before["target"]) == added - set(before["target"])
    second, second_reply = invoke(manifest, packet)
    assert second.returncode == 0, second.stderr + second.stdout
    assert second.stderr == ""
    assert second_reply == expected_reply("unchanged", target, packet["project"], paths)
    assert states(roots) == after


@pytest.mark.parametrize("kind", ["ordinary", "linked"])
def test_distinct_donor_overrides_do_not_redirect_genuine_target(
    tmp_path: Path, request: pytest.FixtureRequest, kind: str,
) -> None:
    original, source, manifest, packet, outside = arrange(tmp_path)
    target = selected_target(tmp_path, original, kind)
    change_target(manifest, packet, target)
    foreign = donor(tmp_path)
    extra = {"GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign)}
    roots = {"target": target, "source": source, "outside": outside, "donor": foreign}
    if kind == "linked":
        roots["primary"] = original
    before = states(roots)
    completed, reply = invoke(manifest, packet, extra)
    observe(request.node, "genuine-donor-" + kind, completed, reply, roots, before)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert reply == expected_reply("restored", target, "ERP", [entry[0] for entry in ENTRIES])
    assert completed.stderr == ""
    for relative, expected, _ in ENTRIES:
        assert (target / relative).read_bytes() == expected
        assert (target / relative).stat().st_mode & 0o777 == 0o600
    after = states(roots)
    for name in roots.keys() - {"target"}:
        assert after[name] == before[name]
    for path, value in before["target"].items():
        assert after["target"][path] == value
    assert set(after["target"]) - set(before["target"]) == {"first.conf", "nested", "nested/second.conf"}
    again, again_reply = invoke(manifest, packet, extra)
    assert again.returncode == 0 and again.stderr == ""
    assert again_reply == expected_reply("unchanged", target, "ERP", [entry[0] for entry in ENTRIES])
    assert states(roots) == after
    if kind == "ordinary":
        assert (target / ".git").is_dir()
    else:
        assert (target / ".git").is_file()


def test_valid_unicode_restore_is_exact_and_repeatable(tmp_path: Path) -> None:
    location = tmp_path / "Склад 🧩 with spaces"
    location.mkdir()
    target, source, manifest, packet, outside = arrange(location)
    declaration = json.loads(manifest.read_text())
    declaration["project"] = packet["project"] = "ERP Склад 🧩"
    declaration["assets"][1]["path"] = "настройки 🧩/политика.conf"
    declaration["assets"][1]["repair"] = "owner repair 🧩"
    manifest.write_text(json.dumps(declaration), encoding="utf-8")
    success_and_repeat(target, source, manifest, packet, outside)


@pytest.mark.parametrize("kind", ["ordinary", "linked"])
def test_existing_ordinary_and_linked_git_roots_are_usable(tmp_path: Path, kind: str) -> None:
    original, source, manifest, packet, outside = arrange(tmp_path)
    target = selected_target(tmp_path, original, kind)
    change_target(manifest, packet, target)
    success_and_repeat(target, source, manifest, packet, outside,
                       primary=original if kind == "linked" else None)


def test_git_root_probe_preserves_caller_environment(tmp_path: Path, monkeypatch) -> None:
    from poise.infrastructure.local_assets import FileLocalAssets
    from poise.modules.local_assets.domain import RestoreRequest

    target, source, _, _, outside = arrange(tmp_path)
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_DIR", str(target / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(target))
    before_environment = dict(os.environ)
    roots = {"target": target, "source": source, "outside": outside}
    before = states(roots)
    with FileLocalAssets().open_roots(RestoreRequest("ERP", str(target), str(source))):
        assert dict(os.environ) == before_environment
    assert dict(os.environ) == before_environment
    assert states(roots) == before


@pytest.mark.parametrize("codepoint", [0xD7FF, 0xE000], ids=["before", "after"])
def test_valid_unicode_interval_neighbours_remain_usable(tmp_path: Path, codepoint: int) -> None:
    target, source, manifest, packet, outside = arrange(tmp_path)
    declaration = json.loads(manifest.read_text())
    declaration["project"] = packet["project"] = "ERP " + chr(codepoint)
    manifest.write_text(json.dumps(declaration), encoding="utf-8")
    success_and_repeat(target, source, manifest, packet, outside)


def test_valid_escaped_surrogate_pair_remains_usable(tmp_path: Path) -> None:
    target, source, manifest, packet, outside = arrange(tmp_path)
    declaration = json.loads(manifest.read_text())
    declaration["project"] = packet["project"] = "ERP 🧩"
    manifest.write_text(json.dumps(declaration), encoding="utf-8")
    assert "\\ud83e\\udde9" in manifest.read_text()
    assert "\\ud83e\\udde9" in json.dumps(packet)
    success_and_repeat(target, source, manifest, packet, outside)
