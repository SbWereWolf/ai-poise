#!/usr/bin/env python3
"""T025 external CLI and fresh-process contracts with test-owned literal oracles.

The checker is never imported. Fixtures are not updated by this runner. Clone
arrangement and direct hashes are test infrastructure, not the subject algorithm.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/delivery/fixtures/application_boundaries"
CHECKER = ROOT / "tools/check_application_boundaries.py"
POLICY = "config/testing/application-boundaries.json"
DOC = "docs/workflows/application-boundaries.md"
EXCLUDED = {"tests", "fixtures", "docs", "__pycache__"}


def data(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.stat().st_mode & 0o777, digest(p))
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


@contextmanager
def checkout(prefix="t025-boundaries-"):
    with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
        root = Path(temporary) / "checkout"
        root.mkdir()
        for name in ("apps", "src"):
            shutil.copytree(ROOT / name, root / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        policy = root / POLICY
        policy.parent.mkdir(parents=True)
        policy.write_bytes((FIXTURES / "policy.json").read_bytes())
        yield root


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def probe(root, fixture=None, *, source=None, filename="boundary_probe.py"):
    path = root / "apps/requirements" / filename
    path.write_text(text(fixture) if fixture is not None else source, encoding="utf-8")
    return path


def run(root, *, encoding="utf-8", arguments=None):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING=encoding)
    command = [sys.executable, "-B", str(CHECKER)]
    command += arguments if arguments is not None else ["--checkout", str(root), "--policy", str(root / POLICY)]
    process = subprocess.run(command, capture_output=True, cwd=root, env=environment, check=False)
    assert process.stderr == b"", process.stderr
    assert process.stdout.count(b"\n") == 1 and process.stdout.endswith(b"\n"), process.stdout
    report = json.loads(process.stdout.decode("utf-8", errors="strict"))
    contract = data("report-contract.json")
    assert set(report) == set(contract["keys"]), report
    assert report["schema"] == "poise-application-boundaries-report/v1"
    assert report["read_only"] is True
    assert report["scope"] == contract["scope"]
    assert set(report["coverage"]) == set(contract["coverage_keys"])
    for name, key in (("files", "file_keys"), ("private_paths", "private_keys"),
                      ("accesses", "access_keys"), ("unresolved_accesses", "unresolved_keys"),
                      ("diagnostics", "diagnostic_keys")):
        for row in report[name]:
            assert set(row) == set(contract[key]), (name, row)
    return process, report


def passed(process, report):
    assert process.returncode == 0, report
    assert report["status"] == "passed" and report["passed"] is True, report
    assert report["stable_inputs"] is True and report["coverage"]["complete"] is True
    assert report["diagnostics"] == []


def rejected(process, report, *, rule=None, kind=None, path=None):
    assert process.returncode == (2 if kind == "invalid" else 1), report
    assert report["passed"] is False, report
    assert report["status"] in ({"invalid"} if kind == "invalid" else {"violation", "inconclusive"})
    rows = report["diagnostics"]
    assert rows, report
    if rule is not None:
        rows = [row for row in rows if row["rule"] == rule]
        assert rows, (rule, report)
    if kind is not None:
        assert any(row["kind"] == kind for row in rows), (kind, report)
    if path is not None:
        assert any(row["file"] == path for row in rows), (path, report)


def independent_sources(root):
    sources = set(data("report-contract.json")["entries"])
    for owner in data("report-contract.json")["owners"]:
        app = root / "apps" / owner
        for path in app.rglob("*.py"):
            if not EXCLUDED.intersection(path.relative_to(app).parts):
                sources.add(path.relative_to(root).as_posix())
    fixture_policy = data("policy.json")["requirements_legacy"]
    modules = fixture_policy["own_modules"] + fixture_policy["shared_modules"]
    for module in modules:
        source = Path("src") / (module.replace(".", "/") + ".py")
        sources.add(source.as_posix())
        for parent in source.parents:
            initializer = parent / "__init__.py"
            if (root / initializer).is_file():
                sources.add(initializer.as_posix())
    return sources


def seven_providers():
    process, report = run(ROOT)
    passed(process, report)
    contract = data("report-contract.json")
    assert report["coverage"]["owners"] == contract["owners"]
    assert report["coverage"]["entrypoints"] == contract["entries"]
    expected_sources = independent_sources(ROOT)
    sources = {row["path"] for row in report["files"] if row["kind"] == "source"}
    configs = {row["path"] for row in report["files"] if row["kind"] == "config"}
    assert sources == expected_sources, (sources ^ expected_sources)
    assert configs == set(contract["config_paths"])
    assert report["coverage"]["source_count"] == len(expected_sources)
    assert report["coverage"]["config_count"] == 7
    assert report["policy_sha256"] == digest(ROOT / POLICY)
    assert json.loads((ROOT / POLICY).read_text()) == data("policy.json")
    for row in report["files"]:
        assert row["sha256"] == digest(ROOT / row["path"]), row
    assert all(not Path(row["path"]).is_absolute() for row in report["private_paths"])


def foreign_import():
    with checkout() as root:
        path = probe(root, "foreign-import.py")
        p, r = run(root)
        rejected(p, r, rule="foreign_import", kind="violation", path=path.relative_to(root).as_posix())


def foreign_legacy_and_exports():
    with checkout() as root:
        for fixture in ("foreign-legacy.py", "foreign-public.py", "foreign-star.py", "foreign-attribute.py"):
            path = probe(root, fixture)
            p, r = run(root)
            rejected(p, r, rule="foreign_import", kind="violation", path=path.relative_to(root).as_posix())


def transitive_import():
    with checkout() as root:
        path = root / "src/poise/modules/tasks/allocation.py"
        path.write_text(path.read_text() + text("foreign-import.py"), encoding="utf-8")
        p, r = run(root)
        rejected(p, r, rule="foreign_import", kind="violation", path=path.relative_to(root).as_posix())
        # A local shadow must be inspected, not silently treated as stdlib json.
        shadow = root / "apps/requirements/json.py"
        shadow.write_text(text("foreign-import.py"), encoding="utf-8")
        p, r = run(root)
        rejected(p, r, rule="foreign_import", kind="violation", path=shadow.relative_to(root).as_posix())


def sqlite_paths():
    with checkout() as root:
        path = probe(root, "foreign-sqlite.py")
        p, r = run(root)
        rejected(p, r, rule="foreign_storage", kind="violation", path=path.relative_to(root).as_posix())
        assert any(a["target_owner"] == "sequencing" and a["access"] == "write"
                   for a in r["accesses"]), r
        probe(root, "unknown-storage.py")
        p, r = run(root)
        rejected(p, r)
        assert any(d["kind"] in {"violation", "inconclusive"} for d in r["diagnostics"])


def filesystem_sinks():
    cases = [
        ('foreign.write_text("x")', "write"), ('foreign.write_bytes(b"x")', "write"),
        ('open(foreign, "w")', "write"), ('foreign.open("a")', "write"),
        ('operating.remove(foreign)', "write"), ('foreign.unlink()', "write"),
        ('foreign.rename(own)', "write"), ('own.rename(foreign)', "write"),
        ('operating.rename(foreign, own)', "write"), ('operating.replace(own, foreign)', "write"),
        ('foreign.replace(own)', "write"), ('own.replace(foreign)', "write"),
        ('foreign.touch()', "write"), ('foreign.mkdir()', "write"),
        ('foreign.read_text()', "read"), ('foreign.read_bytes()', "read"),
        ('open(foreign, "rb")', "read"),
    ]
    with checkout() as root:
        for statement, access in cases:
            path = probe(root, source=text("storage-preamble.py") + statement + "\n")
            p, r = run(root)
            rejected(p, r, rule="foreign_storage", kind="violation", path=path.relative_to(root).as_posix())
            assert any(a["target_owner"] == "sequencing" and a["access"] == access
                       for a in r["accesses"]), (statement, r)
        # An own-module exported store constructor is still a storage sink.
        statement = 'RequirementsStore(foreign, own, 1.0, 0.01)\n'
        path = probe(root, source=(
            'from poise.infrastructure.requirements_registry import RequirementsStore\n'
            + text("storage-preamble.py") + statement))
        p, r = run(root)
        rejected(p, r, rule="foreign_storage", kind="violation", path=path.relative_to(root).as_posix())


def configured_transport():
    with checkout() as root:
        probe(root, "public-argv.py")
        before = snapshot(root)
        p, r = run(root)
        passed(p, r)
        assert snapshot(root) == before


def own_storage_and_no_execution():
    with checkout() as root:
        probe(root, "own-storage.py")
        before = snapshot(root)
        p, r = run(root)
        passed(p, r)
        assert any(a["target"] == "apps/requirements/state/requirements.sqlite"
                   and a["target_owner"] == "requirements" for a in r["accesses"]), r
        assert snapshot(root) == before
        assert not (root / "apps/requirements/state").exists()
        assert r["scope"]["runtime_effects_proven"] is False
        # Existing generic formal-parameter I/O remains disclosed as unresolved.
        assert r["unresolved_accesses"], r


def state_collision_and_alias():
    with checkout() as root:
        path = root / "apps/requirements/config/requirements.json"
        config = json.loads(path.read_text())
        original = path.read_bytes()
        config["paths"]["state"] = "../../sequencing/state"
        write_json(path, config)
        p, r = run(root)
        rejected(p, r, rule="owner_collision", kind="violation")
        path.write_bytes(original)
        (root / "apps/sequencing/state").mkdir()
        (root / "apps/requirements/state").symlink_to(root / "apps/sequencing/state", target_is_directory=True)
        p, r = run(root)
        rejected(p, r)
        assert any(d["rule"] in {"unsafe_path", "owner_collision"} for d in r["diagnostics"])


def invalid_policy():
    with checkout() as root:
        policy_file = root / POLICY
        mutations = [
            lambda p: p["owners"].pop("poise"),
            lambda p: p["owners"].update(extra=p["owners"]["poise"]),
            lambda p: p.update(ignore=["apps/requirements"]),
            lambda p: p["owners"]["poise"].update(root="../poise"),
            lambda p: p["requirements_legacy"]["own_modules"].append("poise.*"),
            lambda p: p["owners"]["requirements"]["path_fields"][1].update(relative_only=False),
            lambda p: p["limits"].update(max_files=True),
            lambda p: p["limits"].update(max_report_bytes=1),
            lambda p: p["limits"].update(max_ast_nodes=1000001),
        ]
        for change in mutations:
            policy = data("policy.json")
            change(policy)
            write_json(policy_file, policy)
            p, r = run(root)
            rejected(p, r, kind="invalid")
        raw = text("policy.json")
        policy_file.write_text(raw.replace('"schema":', '"schema": "duplicate", "schema":', 1))
        p, r = run(root)
        rejected(p, r, kind="invalid")
        policy_file.write_text(raw.replace('"max_files": 256', '"max_files": NaN'))
        p, r = run(root)
        rejected(p, r, kind="invalid")
        policy_file.write_text(raw)
        p, r = run(root, arguments=["--checkout", str(root)])
        rejected(p, r, kind="invalid")
        p, r = run(root, arguments=["--checkout", str(root), "--policy", str(root / "absent.json")])
        rejected(p, r, kind="invalid")
        assert r["policy_sha256"] is None


def incomplete_sources():
    with checkout() as root:
        provider = root / "apps/bootstrap/bin/bootstrap"
        raw = provider.read_bytes()
        provider.unlink()
        p, r = run(root)
        rejected(p, r, kind="inconclusive")
        assert r["coverage"]["complete"] is False
        provider.write_bytes(raw)
        path = probe(root, "syntax-error.py")
        p, r = run(root)
        rejected(p, r, rule="invalid_source", kind="inconclusive", path=path.relative_to(root).as_posix())
        path.unlink()
        with tempfile.TemporaryDirectory(prefix="t025-external-") as temp:
            outside = Path(temp) / "outside.py"
            outside.write_text("value = 1\n")
            path.symlink_to(outside)
            p, r = run(root)
            rejected(p, r, rule="unsafe_path", kind="inconclusive")
        path.unlink()
        shell = root / "apps/poise/shell.py"
        shell.unlink()
        p, r = run(root)
        rejected(p, r, kind="inconclusive")


def relocation_and_encoding():
    with checkout(prefix="t025 проба é ") as root:
        path = probe(root, "non-ascii.py", filename="проба-é.py")
        for encoding in ("ascii", "latin-1"):
            p, r = run(root, encoding=encoding)
            passed(p, r)
            assert "проба-é.py" in p.stdout.decode("utf-8")
            assert str(root) not in p.stdout.decode("utf-8"), r
            assert any(row["path"] == path.relative_to(root).as_posix() for row in r["files"])


def stable_hashes_and_no_mutation():
    with checkout() as root:
        probe(root, "own-storage.py")
        sentinels = [root / "apps/sequencing/state/foreign.sqlite", root / "apps/poise/results/sentinel"]
        for path in sentinels:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test-owned-private-state\x00")
        before = snapshot(root)
        first, report = run(root)
        passed(first, report)
        second, other = run(root)
        passed(second, other)
        assert first.stdout == second.stdout
        assert snapshot(root) == before
        for row in report["files"]:
            assert row["sha256"] == hashlib.sha256((root / row["path"]).read_bytes()).hexdigest()
        assert report["policy_sha256"] == digest(root / POLICY)
        assert not list(root.rglob("__pycache__"))


def unsupported_imports_and_loaders():
    with checkout() as root:
        p, r = run(root)
        passed(p, r)  # includes the actual own Bootstrap loader, not a mock
        for fixture in ("dynamic-import.py", "dynamic-loader.py", "path-mutation.py"):
            path = probe(root, fixture)
            p, r = run(root)
            rejected(p, r, kind="inconclusive", path=path.relative_to(root).as_posix())
        probe(root, source="module = __import__(input())\n")
        p, r = run(root)
        rejected(p, r, kind="inconclusive")


def explicit_limits():
    with checkout() as root:
        for key, value in (("max_files", 1), ("max_file_bytes", 1),
                           ("max_total_bytes", 1), ("max_ast_nodes", 1),
                           ("max_report_bytes", 1024)):
            policy = data("policy.json")
            policy["limits"][key] = value
            write_json(root / POLICY, policy)
            p, r = run(root)
            rejected(p, r, kind="inconclusive")
            assert r["coverage"]["complete"] is False
            if key == "max_report_bytes":
                assert len(p.stdout) <= value
        policy = data("policy.json")
        policy["limits"]["max_diagnostics"] = 1
        write_json(root / POLICY, policy)
        probe(root, source=text("foreign-import.py") + text("foreign-legacy.py"))
        p, r = run(root)
        rejected(p, r)
        assert len(r["diagnostics"]) <= 1
        assert r["coverage"]["complete"] is False


def import_probe(mode):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for key in ("PYTHONHOME", "PYTHONPATH"):
        environment.pop(key, None)
    proc = subprocess.run([sys.executable, "-I", "-B", str(FIXTURES / "imports-probe.py"),
                           str(ROOT), mode, str(FIXTURES / "parser-cases.json")],
                          capture_output=True, cwd=ROOT, env=environment, check=False)
    assert proc.returncode == 0 and proc.stderr == b"", (proc.returncode, proc.stdout, proc.stderr)
    return json.loads(proc.stdout.decode("utf-8"))


def requirement_imports():
    result = import_probe("requirements")
    assert result["unexpected"] == [], "unexpected local imports: " + ", ".join(result["unexpected"])


def bare_imports():
    result = import_probe("bare")
    assert result["unexpected"] == [], "unexpected local imports: " + ", ".join(result["unexpected"])


def public_api():
    assert import_probe("api") == {"passed": True}


def legacy_parser():
    assert import_probe("legacy") == {"passed": True}


def helper_reexport():
    relative = "src/poise/modules/foundation/strict_json.py"
    assert (ROOT / relative).is_file(), "missing helper: " + relative
    assert import_probe("helper") == {"passed": True}


def doc_contains(needles):
    page = (ROOT / DOC).read_text(encoding="utf-8")
    for needle in needles:
        assert needle in page, "missing documentation: " + needle
    return page


def doc_command():
    doc_contains(["tools/check_application_boundaries.py", "--checkout", "--policy", POLICY,
                  "poise-application-boundaries-report/v1", "passed", "inconclusive", "invalid",
                  "exit 0", "exit 1", "exit 2"])


def doc_owners():
    contract = data("report-contract.json")
    doc_contains(contract["entries"] + contract["config_paths"] +
                 ["private", "input_roots", "projects_root", "session_store", "JSON", "CLI"])


def doc_limits_and_compatibility():
    doc_contains(["runtime_effects_proven", "root_aliases_checked", "unresolved_accesses",
                  "read_only", "sys.path", "rename", "replace", "strict_json", "PoiseError",
                  "__getattr__", "max_files", "max_ast_nodes", "max_report_bytes"])


def doc_links():
    doc_contains(["T072", "T085", "stride"])
    page = (ROOT / "apps/README.md").read_text(encoding="utf-8")
    assert "application-boundaries.md" in page
    # Reuse the existing documentation owner; do not introduce another scanner.
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/check_documentation.py"),
                           "--checkout", str(ROOT), "--changed", DOC, "--changed", "apps/README.md"],
                          capture_output=True, cwd=ROOT, env=env, check=False)
    assert proc.returncode == 0 and proc.stderr == b"", (proc.returncode, proc.stdout, proc.stderr)
    result = json.loads(proc.stdout)
    assert result["errors"] == [], result


BEHAVIOR = [
    ("boundaries.seven_providers", seven_providers),
    ("boundaries.foreign_import", foreign_import),
    ("boundaries.legacy_import", foreign_legacy_and_exports),
    ("boundaries.transitive_import", transitive_import),
    ("boundaries.sqlite", sqlite_paths),
    ("boundaries.filesystem", filesystem_sinks),
    ("boundaries.public_transport", configured_transport),
    ("boundaries.own_storage", own_storage_and_no_execution),
    ("boundaries.owner_collision", state_collision_and_alias),
    ("boundaries.invalid_policy", invalid_policy),
    ("boundaries.coverage", incomplete_sources),
    ("boundaries.encoding", relocation_and_encoding),
    ("boundaries.stability", stable_hashes_and_no_mutation),
    ("boundaries.dynamic", unsupported_imports_and_loaders),
    ("boundaries.limits", explicit_limits),
]
IMPORTS = [
    ("imports.requirements_isolation", requirement_imports),
    ("imports.bare_package", bare_imports),
    ("imports.public_api", public_api),
    ("imports.legacy_parser", legacy_parser),
    ("imports.helper_reexport", helper_reexport),
]
DOCUMENTATION = [
    ("docs.command", doc_command), ("docs.owners", doc_owners),
    ("docs.limits", doc_limits_and_compatibility), ("docs.links", doc_links),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, choices=("behavior", "imports", "documentation"))
    args = parser.parse_args()
    errors, failures = [], []
    tests = {"behavior": BEHAVIOR, "imports": IMPORTS, "documentation": DOCUMENTATION}[args.suite]
    if args.suite == "behavior" and not CHECKER.is_file():
        failures = [{"reason": "missing checker: tools/check_application_boundaries.py",
                     "test": "boundaries.provider"}]
        count = 1
    else:
        for name, test in tests:
            try:
                test()
            except AssertionError as exc:
                failures.append({"reason": str(exc) or "assertion failed", "test": name})
            except Exception as exc:
                errors.append({"reason": f"{type(exc).__name__}: {exc}", "test": name})
        count = len(tests)
    result = {"errors": errors, "failures": failures, "tests_run": count}
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    return int(bool(errors or failures))


if __name__ == "__main__":
    raise SystemExit(main())
