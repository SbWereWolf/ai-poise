from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[2]
LEGACY_WORD = "har" + "ness"
LEGACY_IMPORT = LEGACY_WORD
LEGACY_DISTRIBUTION = "agent-" + LEGACY_WORD + "-happy-path"
NEW_DISTRIBUTION = "ai-poise"
NEW_IMPORT = "poise"
LEGACY_PUBLIC = "Har" + "ness"
LEGACY_ERROR = LEGACY_PUBLIC + "Error"
SUBCOMMANDS = (
    "project",
    "project-init",
    "work",
    "runtime",
    "goal-config",
    "catalogue",
    "runtime-setup",
    "runtime-config",
    "hook",
    "hook-work",
)


def _isolated_environment(environment=None):
    isolated = dict(os.environ if environment is None else environment)
    for variable in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        isolated.pop(variable, None)
    return isolated


def _run(argv, *, cwd=None, environment=None, stdin=None, timeout=180):
    result = subprocess.run(
        [str(value) for value in argv], cwd=cwd,
        env=_isolated_environment(environment), input=stdin, text=True,
        capture_output=True, timeout=timeout,
    )
    assert result.returncode == 0, (
        f"command failed ({result.returncode}): {argv}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


def _copy_build_source(destination: Path) -> Path:
    target = destination / "source"
    shutil.copytree(
        ROOT, target,
        ignore=shutil.ignore_patterns(
            ".git", ".pytest_cache", "__pycache__", "*.pyc", "*.egg-info", "build", "dist"
        ),
    )
    return target


def _build_wheel(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    before = set(destination.glob("*.whl"))
    build_environment = destination.parent / "build-venv"
    _run((sys.executable, "-m", "venv", build_environment), timeout=180)
    _run((_venv_python(build_environment), "-m", "pip", "wheel", "--no-deps",
          "--wheel-dir", destination, source), timeout=300)
    created = set(destination.glob("*.whl")) - before
    assert len(created) == 1, f"expected one wheel, got {sorted(created)}"
    return created.pop()


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_script(environment: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return environment / ("Scripts" if os.name == "nt" else "bin") / (name + suffix)


def _install_wheel(environment: Path, wheel: Path) -> None:
    if not environment.exists():
        _run((sys.executable, "-m", "venv", environment), timeout=180)
    _run((_venv_python(environment), "-m", "pip", "install", "--no-index", wheel), timeout=300)


@pytest.fixture(scope="module")
def installed_poise(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("installed-poise")
    wheel = _build_wheel(_copy_build_source(workspace), workspace / "wheels")
    environment = workspace / "venv"
    _install_wheel(environment, wheel)
    return {"workspace": workspace, "wheel": wheel, "venv": environment}


def test_python_namespace_is_poise():
    assert (ROOT / "src" / NEW_IMPORT / "__init__.py").is_file(), (
        "src/poise is required before production implementation can pass"
    )
    assert not (ROOT / "src" / LEGACY_IMPORT).exists()
    command = (
        sys.executable, "-I", "-S", "-c",
        f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r}); "
        f"import {NEW_IMPORT}; print({NEW_IMPORT}.__name__)",
    )
    assert _run(command, cwd=ROOT).stdout.strip() == NEW_IMPORT
    rejected = subprocess.run(
        (sys.executable, "-I", "-S", "-c",
         f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r}); import {LEGACY_IMPORT}"),
        cwd=ROOT, env=_isolated_environment(), text=True, capture_output=True, timeout=30,
    )
    assert rejected.returncode != 0
    assert "ModuleNotFoundError" in rejected.stderr


def test_public_python_entities_are_poise():
    command = (
        sys.executable, "-I", "-S", "-c",
        f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r}); "
        "import poise; from poise import Poise, PoiseError; "
        "from poise.runtime import Poise as RuntimePoise; "
        "from poise.modules.foundation.errors import PoiseError as FoundationPoiseError; "
        "assert Poise is RuntimePoise; assert PoiseError is FoundationPoiseError; "
        f"assert not hasattr(poise, {LEGACY_PUBLIC!r}); "
        f"assert not hasattr(poise, {LEGACY_ERROR!r})",
    )
    _run(command, cwd=ROOT)


def test_fresh_wheel_exposes_only_poise(installed_poise):
    environment = installed_poise["venv"]
    python = _venv_python(environment)
    script = _venv_script(environment, NEW_IMPORT)
    assert script.is_file()
    assert not _venv_script(environment, LEGACY_IMPORT).exists()
    metadata = _run((python, "-c", "import importlib.metadata as m; "
                     f"print(m.distribution({NEW_DISTRIBUTION!r}).metadata['Name'])"))
    assert metadata.stdout.strip() == NEW_DISTRIBUTION
    _run((python, "-m", NEW_IMPORT, "--help"))
    legacy = subprocess.run(
        (python, "-c", "import importlib.metadata as m; "
         f"m.distribution({LEGACY_DISTRIBUTION!r})"),
        env=_isolated_environment(), text=True, capture_output=True, timeout=30,
    )
    assert legacy.returncode != 0
    legacy_import = subprocess.run(
        (python, "-c", f"import {LEGACY_IMPORT}"),
        env=_isolated_environment(), text=True, capture_output=True, timeout=30,
    )
    assert legacy_import.returncode != 0


def test_poise_help_exposes_every_supported_subcommand(installed_poise):
    script = _venv_script(installed_poise["venv"], NEW_IMPORT)
    assert script.is_file()
    top = _run((script, "--help"))
    for command in SUBCOMMANDS:
        assert command in top.stdout
        _run((script, command, "--help"))
    assert LEGACY_IMPORT not in top.stdout.lower()


def _legacy_revision() -> str:
    for revision in _run(("git", "rev-list", "--reverse", "HEAD"), cwd=ROOT).stdout.splitlines():
        metadata = subprocess.run(
            ("git", "show", f"{revision}:pyproject.toml"), cwd=ROOT,
            env=_isolated_environment(), text=True, capture_output=True, timeout=30,
        )
        if metadata.returncode == 0 and LEGACY_DISTRIBUTION in metadata.stdout:
            return revision
    pytest.fail("no reachable real legacy distribution revision was found")


def _extract_revision(revision: str, destination: Path) -> Path:
    archive = subprocess.run(
        ("git", "archive", "--format=tar", revision), cwd=ROOT,
        env=_isolated_environment(), capture_output=True, timeout=60,
    )
    assert archive.returncode == 0, archive.stderr.decode(errors="replace")
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
        bundle.extractall(destination, filter="data")
    return destination


def test_replacement_upgrade_removes_legacy_distribution_and_script(installed_poise):
    workspace = installed_poise["workspace"] / "replacement"
    legacy_wheel = _build_wheel(
        _extract_revision(_legacy_revision(), workspace / "legacy-source"),
        workspace / "legacy-wheels",
    )
    environment = workspace / "venv"
    _install_wheel(environment, legacy_wheel)
    legacy_script = _venv_script(environment, LEGACY_IMPORT)
    assert legacy_script.is_file()
    _run((legacy_script, "--help"))
    python = _venv_python(environment)
    upgrade_tool = ROOT / "tools" / "upgrade_to_ai_poise.py"
    assert upgrade_tool.is_file(), "one executable replacement procedure is required"
    _run((sys.executable, upgrade_tool, "--python", python, "--wheel", installed_poise["wheel"]),
         timeout=600)
    assert not legacy_script.exists()
    assert _venv_script(environment, NEW_IMPORT).is_file()
    _run((_venv_script(environment, NEW_IMPORT), "--help"))
    rejected = subprocess.run(
        (python, "-c", f"import {LEGACY_IMPORT}"),
        env=_isolated_environment(), text=True, capture_output=True, timeout=30,
    )
    assert rejected.returncode != 0
    legacy_metadata = subprocess.run(
        (python, "-c", "import importlib.metadata as m; "
         f"m.distribution({LEGACY_DISTRIBUTION!r})"),
        env=_isolated_environment(), text=True, capture_output=True, timeout=30,
    )
    assert legacy_metadata.returncode != 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task_and_sprint_ids(database: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        tasks = tuple(row[0] for row in connection.execute("SELECT id FROM tasks ORDER BY id")) \
            if "tasks" in tables else ()
        sprints = tuple(row[0] for row in connection.execute("SELECT id FROM sprints ORDER BY id")) \
            if "sprints" in tables else ()
        return tasks, sprints
    finally:
        connection.close()


def test_taskless_cycle_uses_copied_existing_state_without_mutating_source(installed_poise, tmp_path):
    source_config_root = ROOT / "config" / "projects" / "ai-poise"
    source_config = source_config_root / "project.json"
    config = json.loads(source_config.read_text())
    configured_state = Path(config["paths"]["state"]).resolve(strict=True)
    database_relative = Path(config["paths"]["database"])
    assert not database_relative.is_absolute()
    configured_database = configured_state / database_relative
    assert configured_database.is_file()
    source_state = tmp_path / "observed-source"
    source_database = source_state / database_relative
    source_database.parent.mkdir(parents=True)
    with sqlite3.connect(
        f"file:{configured_database}?mode=ro", uri=True
    ) as observed, sqlite3.connect(source_database) as snapshot:
        observed.backup(snapshot)
    before_hash = _sha256(source_database)
    before_ids = _task_and_sprint_ids(source_database)

    copied_config_root = tmp_path / "config"
    shutil.copytree(source_config_root, copied_config_root)
    copied_state = tmp_path / "state"
    copied_database = copied_state / database_relative
    copied_database.parent.mkdir(parents=True)
    shutil.copy2(source_database, copied_database)
    config["paths"]["state"] = str(copied_state)
    config["git"]["repository"] = str(ROOT)
    copied_config = copied_config_root / "project.json"
    copied_config.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")

    environment = {**os.environ, "POISE_CONFIG": str(copied_config),
                   "POISE_SESSION": "taskless-copy-" + uuid.uuid4().hex}
    script = _venv_script(installed_poise["venv"], NEW_IMPORT)
    assert script.is_file()
    bootstrap = _run(
        (script, "work"), cwd=ROOT, environment=environment,
        stdin=json.dumps({"operation": "bootstrap", "input": {
            "task": None, "decision": None, "feedback": None, "rework_stage": None}, "messages": []}),
    )
    assert json.loads(bootstrap.stdout)["status"] == "read_only"
    verified = _run(
        (script, "work"), cwd=ROOT, environment=environment,
        stdin=json.dumps({"operation": "verify", "input": {"result": None, "artifacts": []},
                          "messages": []}),
    )
    result = json.loads(verified.stdout)
    assert result["status"] == "read_only_verified"
    assert result["checks"] == []
    assert _task_and_sprint_ids(copied_database) == before_ids
    assert _sha256(source_database) == before_hash
    assert _task_and_sprint_ids(source_database) == before_ids
