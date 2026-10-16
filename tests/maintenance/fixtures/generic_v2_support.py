"""Transparent test-owned arrangement for the actual external CLI and Bootstrap."""

import json
import os
from pathlib import Path
import subprocess
import venv

import pytest

REPO = Path(__file__).resolve().parents[3]
ACTION = Path(__file__).with_name("v2_action.py")


@pytest.fixture(scope="session")
def consumer(tmp_path_factory):
    wheel = Path(os.environ["AIP_GENERIC_WHEEL"])
    root = tmp_path_factory.mktemp("selected-generic-v2")
    venv.EnvBuilder(with_pip=True).create(root)
    python = root / "bin/python"
    installed = subprocess.run(
        [str(python), "-I", "-B", "-m", "pip", "install",
         "--no-index", "--no-deps", str(wheel)], capture_output=True)
    assert installed.returncode == 0, (installed.stdout,
                                       installed.stderr)
    probe = subprocess.run([str(python), "-I", "-B", "-c",
                            "import importlib.metadata as m,environment_maintenance as e; "
                            "print(m.version('environment-maintenance')); print(e.__file__)"],
                           capture_output=True)
    assert probe.returncode == 0 and probe.stderr == b"", (
        probe.stdout, probe.stderr)
    version, origin = probe.stdout.decode("utf-8").splitlines()
    assert version == "0.2.1"
    assert Path(origin).is_relative_to(root)
    return python


@pytest.fixture
def catalog(tmp_path, consumer):
    data = json.loads(ACTION.with_name("v2_catalog.json").read_text(
        encoding="utf-8"))
    for role in ("check", "install", "repair"):
        action = data["requirements"][0][role]
        action["argv"] = [str(consumer), "-I", "-B", str(ACTION),
                          str(tmp_path), role, "resource", "normal"]
        action["cwd"] = str(tmp_path)
    (tmp_path / "unrelated").write_text("preserve", encoding="utf-8")
    return tmp_path, data


def invoke(consumer, catalog, verb="run", *, extra=(), env=None):
    root, data = catalog
    (root / "catalog.json").write_text(json.dumps(data),
                                       encoding="utf-8")
    return invoke_raw(consumer, root, verb, extra=extra, env=env)


def invoke_raw(consumer, root, verb="run", *, extra=(), env=None):
    return subprocess.run(
        [str(consumer), "-I", "-B", "-m", "environment_maintenance",
         verb, "--catalog-dir", str(root), *extra],
        cwd=root, env=env, capture_output=True)


def report(result, code, status):
    assert result.returncode == code, (result.stdout, result.stderr)
    assert result.stderr == b""
    packet = json.loads(
        result.stdout.decode("utf-8", errors="strict"))
    assert packet["schema"] == "environment-maintenance/result/v2"
    assert packet["status"] == status
    assert packet["exit_code"] == code
    return packet


def calls(root):
    path = root / "calls.jsonl"
    return [json.loads(line) for line in path.read_text(
        encoding="utf-8").splitlines()] if path.exists() else []


def state(root, value):
    if value != "missing":
        (root / "resource").write_text(
            "valid" if value == "satisfied" else "bad",
            encoding="utf-8")


def bootstrap(consumer, catalog, operation="infra", *, extra=(),
              env=None, write=True):
    root, data = catalog
    if write:
        (root / "catalog.json").write_text(json.dumps(data),
                                           encoding="utf-8")
    # The real shipped sample is the selected configuration subject, not an oracle.
    config = json.loads(
        (REPO / "config/bootstrap.example.json").read_text(
            encoding="utf-8"))
    values = root / "command-values.json"
    values.write_text("{}", encoding="utf-8")
    config["parameters"] = {"python": str(consumer),
                            "tool_config": str(values)}
    selected = root / "command.json"
    selected.write_text(json.dumps(config), encoding="utf-8")
    return subprocess.run(
        [str(consumer), "-I", "-B", str(REPO / "bootstrap"),
         operation,
         "--config", str(selected), "--catalog-dir", str(root),
         *extra],
        cwd=root, env=env, capture_output=True)


def outer(result, code, status, stages, actions):
    assert result.returncode == code and result.stderr == b"", (
        result.stdout, result.stderr)
    packet = json.loads(
        result.stdout.decode("utf-8", errors="strict"))
    assert packet["status"] == status and packet["exit_code"] == code
    assert [row["stage"] for row in packet["observations"]] == stages
    assert packet["actions"] == actions
    return packet


def underlying(observation, code, status):
    assert observation["exit_code"] == code
    assert observation["stderr"] == ""
    packet = json.loads(observation["stdout"])
    assert packet["schema"] == "environment-maintenance/result/v2"
    assert packet["status"] == status and packet["exit_code"] == code
    return packet
