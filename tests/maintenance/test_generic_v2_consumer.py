"""Real installed generic v2 consumer; expectations belong to these tests."""
import hashlib
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import venv
import zipfile

import pytest


REPO = Path(__file__).resolve().parents[2]
ACTION = Path(__file__).with_name("fixtures") / "v2_action.py"


@pytest.fixture(scope="module")
def consumer(tmp_path_factory):
    wheel = Path(os.environ["AIP_GENERIC_WHEEL"])
    build = json.loads(Path(os.environ["AIP_GENERIC_BUILD"]).read_text())
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == build["wheel_sha256"]
    assert build["source_commit"] == "482b11417f8e74d9bcc1d885767bb16837f3284a"
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == "01932116ea61f7da6f6f613ce910bd978a5b46fb91ab1d14b74b9c6ad78e3ba7"
    with zipfile.ZipFile(wheel) as package:
        metadata = package.read("environment_maintenance-0.2.1.dist-info/METADATA")
        assert b"Name: environment-maintenance\n" in metadata
        assert b"Version: 0.2.1\n" in metadata
        assert b"Requires-Python: >=3.11\n" in metadata
        assert not any(name.startswith("poise/") for name in package.namelist())
    root = tmp_path_factory.mktemp("generic-v2-interpreter")
    venv.EnvBuilder(with_pip=True).create(root)
    python = root / "bin/python"
    installed = subprocess.run([str(python), "-I", "-B", "-m", "pip", "install",
                                "--no-index", "--no-deps", str(wheel)], capture_output=True)
    assert installed.returncode == 0, (installed.stdout, installed.stderr)
    layout = root / "bootstrap-layout"
    layout.mkdir()
    shutil.copy2(REPO / "bootstrap", layout / "bootstrap")
    shutil.copytree(REPO / "apps/bootstrap", layout / "apps/bootstrap")
    # Conditional transport guard: test-owned selection, never the repository lock.
    shutil.copy2(ACTION.parent / "v2_selected_component.json",
                 layout / "apps/bootstrap/component.json")
    identity = subprocess.run([str(python), "-I", "-B", "-c",
        "import importlib.metadata as m,environment_maintenance as e; "
        "print(m.version('environment-maintenance')); print(e.__file__)"], capture_output=True)
    assert identity.returncode == 0, (identity.stdout, identity.stderr)
    assert identity.stderr == b""
    version, origin = identity.stdout.decode().splitlines()
    assert version == "0.2.1"
    assert Path(origin).is_relative_to(root)
    with zipfile.ZipFile(wheel) as package:
        for name in package.namelist():
            if name.startswith("environment_maintenance/") and name.endswith(".py"):
                assert (Path(origin).parent.parent / name).read_bytes() == package.read(name)
    return python


@pytest.fixture
def catalog(tmp_path, consumer):
    data = json.loads((ACTION.parent / "v2_catalog.json").read_text())
    for role in ("check", "install", "repair"):
        action = data["requirements"][0][role]
        action["argv"] = [str(consumer), "-I", "-B", str(ACTION),
                          str(tmp_path), role, "resource", "normal"]
        action["cwd"] = str(tmp_path)
    (tmp_path / "unrelated").write_text("preserve")
    return tmp_path, data


def invoke(consumer, catalog, verb="run", *, bootstrap=False, extra=(), env=None,
           selected_repository=False):
    root, data = catalog
    (root / "catalog.json").write_text(json.dumps(data))
    bootstrap_root = REPO if selected_repository else consumer.parent.parent / "bootstrap-layout"
    entry = [str(bootstrap_root / "bootstrap")] if bootstrap else ["-m", "environment_maintenance"]
    return subprocess.run([str(consumer), "-I", "-B", *entry, verb,
                           "--catalog-dir", str(root), *extra],
                          cwd=root, env=env, capture_output=True)


def report(result, code, status):
    assert result.returncode == code, (result.stdout, result.stderr)
    assert result.stderr == b""
    data = json.loads(result.stdout)
    assert data["schema"] == "environment-maintenance/result/v2"
    assert data["status"] == status
    assert data["exit_code"] == code
    return data


def calls(root):
    path = root / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def state(root, value):
    if value != "missing":
        (root / "resource").write_text("valid" if value == "satisfied" else "bad")


@pytest.mark.parametrize("initial,roles", [("satisfied", ["check"]),
    ("missing", ["check", "install", "check"]),
    ("invalid", ["check", "repair", "check"])])
def test_real_state_selection_and_original_recheck(consumer, catalog, initial, roles):
    root, _ = catalog
    state(root, initial)
    row = report(invoke(consumer, catalog), 0, "ready")["requirements"][0]
    assert row["initial_state"] == initial
    assert row["state"] == "satisfied"
    observed = calls(root)
    assert [item["role"] for item in observed] == roles
    assert observed[0] == observed[-1]
    if initial != "satisfied":
        assert row["initial_check"]["invocation"] == row["final_check"]["invocation"]
        assert row["mutation"]["response"]["action"] == roles[1]
    assert (root / "resource").read_text() == "valid"
    assert (root / "unrelated").read_text() == "preserve"


def test_unavailable_preserves_guidance_and_never_mutates(consumer, catalog):
    root, data = catalog
    data["requirements"][0]["check"]["argv"][-1] = "unavailable"
    row = report(invoke(consumer, catalog), 3, "unavailable")["requirements"][0]
    assert row["initial_state"] == "unavailable"
    assert row["cause"] == "Resource is offline"
    assert row["recommendations"] == ["Restore access"]
    assert [item["role"] for item in calls(root)] == ["check"]
    assert not (root / "resource").exists()


@pytest.mark.parametrize("initial", ["missing", "invalid", "satisfied", "unavailable"])
@pytest.mark.parametrize("verb,extra", [("check", ()), ("run", ("--dry-run",))])
def test_diagnostic_resource_snapshot(consumer, catalog, initial, verb, extra):
    root, data = catalog
    state(root, initial)
    if initial == "unavailable":
        data["requirements"][0]["check"]["argv"][-1] = "unavailable"
    before = {p.name: (p.read_bytes(), p.stat().st_mode) for p in root.iterdir()}
    code, status = ((0, "ready") if initial == "satisfied" else
                    (3, "unavailable") if initial == "unavailable" else (2, "action_required"))
    row = report(invoke(consumer, catalog, verb, extra=extra), code, status)["requirements"][0]
    for name, expected in before.items():
        p = root / name
        assert (p.read_bytes(), p.stat().st_mode) == expected
    assert (root / "resource").exists() == (initial != "missing")
    assert [item["role"] for item in calls(root)] == ["check"]
    assert "mutation" not in row


@pytest.mark.parametrize("initial,role", [("missing", "install"), ("invalid", "repair")])
@pytest.mark.parametrize("mode", ["fail", "no-fix", "malformed", "stdout=513", "stderr=513", "timeout"])
def test_mutation_failure_keeps_original_recheck(consumer, catalog, initial, role, mode):
    root, data = catalog
    state(root, initial)
    action = data["requirements"][0][role]
    action["argv"][-1] = mode
    action["max_output_bytes"] = 512
    if mode == "timeout":
        action["execution_bound_seconds"] = .5
    row = report(invoke(consumer, catalog), 4, "failed")["requirements"][0]
    observed = calls(root)
    assert [item["role"] for item in observed] == ["check", role, "check"]
    assert observed[0] == observed[-1]
    assert row["initial_check"]["invocation"] == row["final_check"]["invocation"]
    assert row["status"] == "failed"
    if mode == "no-fix":
        assert row["mutation"]["status"] == "passed"
        assert row["state"] == initial
    else:
        assert row["mutation"]["status"] == "protocol_failure"
    if mode == "timeout":
        assert row["mutation"]["timed_out"] is True
        assert (root / "started").exists()
        pid = int((root / "started").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        time.sleep(2)
        assert not (root / "late-effect").exists()


@pytest.mark.parametrize("stream", ["stdout", "stderr", "both"])
@pytest.mark.parametrize("size", [511, 512, 513])
def test_per_stream_output_boundaries(consumer, catalog, stream, size):
    root, data = catalog
    state(root, "satisfied")
    action = data["requirements"][0]["check"]
    action["argv"][-1] = f"{stream}={size}"
    action["max_output_bytes"] = 512
    row = report(invoke(consumer, catalog), 4 if size == 513 else 0,
                 "failed" if size == 513 else "ready")["requirements"][0]
    check = row["initial_check"]
    exact_streams = (("stderr",) if stream == "both" and size == 513 else
                     ("stdout", "stderr") if stream == "both" else (stream,))
    for name in exact_streams:
        assert len(check[name].encode()) == min(size, 512)
    if stream == "both" and size == 513:
        assert len(check["stdout"].encode()) <= 512
    assert check["capture_complete"] is (size <= 512)
    assert check["timed_out"] is False
    assert check["status"] == ("protocol_failure" if size == 513 else "passed")
    if size == 513:
        assert check["error"] == "Action exceeded max_output_bytes"
        assert "response" not in check
    assert [item["role"] for item in calls(root)] == ["check"]


def test_initial_timeout_has_no_mutation_or_late_effect(consumer, catalog):
    root, data = catalog
    action = data["requirements"][0]["check"]
    action["argv"][-1] = "timeout"
    action["execution_bound_seconds"] = .5
    check = report(invoke(consumer, catalog), 4, "failed")["requirements"][0]["initial_check"]
    assert check["status"] == "protocol_failure"
    assert check["timed_out"] is True
    assert check["capture_complete"] is True
    assert check["error"] == "Action exceeded execution_bound_seconds"
    assert "response" not in check
    pid = int((root / "started").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    time.sleep(2)
    assert not (root / "late-effect").exists()
    assert not (root / "resource").exists()
    assert [item["role"] for item in calls(root)] == ["check"]


@pytest.mark.parametrize("bootstrap,verb", [(False, "check"), (False, "run"),
    (True, "check"), (True, "infra"), (True, "deps")])
@pytest.mark.parametrize("legacy", ["option", "apply-verb", "apply-field", "routing", "old-profile", "old-file"])
def test_retired_inputs_rejected_before_action(consumer, catalog, bootstrap, verb, legacy):
    root, data = catalog
    extra = ()
    if legacy == "option":
        extra = ("--repairs", str(root / "repairs.json"))
    elif legacy == "apply-verb":
        verb = "apply"
    elif legacy == "apply-field":
        data["requirements"][0]["apply"] = data["requirements"][0]["install"]
    elif legacy == "routing":
        data["repairs"] = json.loads((ACTION.parent / "v1_poison_repairs.json").read_text())["repairs"]
    elif legacy == "old-profile":
        data = json.loads((ACTION.parent / "v1_poison_requirements.json").read_text())
        catalog = root, data
    elif legacy == "old-file":
        extra = ("--catalog-dir", str(root / "requirements.json"))
    result = invoke(consumer, catalog, verb, bootstrap=bootstrap, extra=extra)
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr
    assert calls(root) == []
    assert not (root / "resource").exists()


@pytest.mark.parametrize("verb,bootstrap", [("run", False), ("infra", True), ("deps", True)])
def test_unselected_legacy_neighbors_never_substitute_v2(consumer, catalog, verb, bootstrap):
    root, _ = catalog
    for name in ("requirements.json", "repairs.json", "repair-routing.json"):
        (root / name).write_text("INVALID LEGACY JSON: poison")
    row = report(invoke(consumer, catalog, verb, bootstrap=bootstrap), 0, "ready")["requirements"][0]
    assert row["id"] == "resource"
    assert [item["role"] for item in calls(root)] == ["check", "install", "check"]
    for name in ("requirements.json", "repairs.json", "repair-routing.json"):
        assert (root / name).read_text() == "INVALID LEGACY JSON: poison"


@pytest.mark.parametrize("origin", ["default", "values", "environment", "cli"])
def test_explicit_environment_and_parameter_precedence(consumer, catalog, origin):
    root, data = catalog
    data["parameters"] = json.loads((ACTION.parent / "v2_parameters.json").read_text())
    data["inherit_env"] = ["V2_ALLOWED"]
    for role in ("check", "install", "repair"):
        data["requirements"][0][role]["env"] = {
            "BOUND": "${selected}", "COUNT": "${count}",
            "ENABLED": "${enabled}", "RATIO": "${ratio}"}
    values = root / "values.json"
    values.write_text('{"selected":"values"}')
    extra = () if origin == "default" else ("--values", str(values))
    env = {"V2_ALLOWED": "allowed", "V2_SECRET": "unselected"}
    if origin in ("environment", "cli"):
        env["V2_SELECTED"] = "environment"
    if origin == "cli":
        extra += ("--set", "selected=cli")
    result = report(invoke(consumer, catalog, extra=extra, env=env), 0, "ready")
    assert result["parameters"]["selected"] == origin
    assert result["parameter_sources"]["selected"] == origin
    assert result["parameters"]["count"] == 3
    assert result["parameters"]["enabled"] is True
    assert result["parameters"]["ratio"] == 1.25
    for item in calls(root):
        assert item["env"]["BOUND"] == origin
        assert item["env"]["COUNT"] == "3"
        assert item["env"]["ENABLED"] == "true"
        assert item["env"]["RATIO"] == "1.25"
        assert item["env"]["V2_ALLOWED"] == "allowed"
        assert "V2_SECRET" not in item["env"]
        assert "V2_SELECTED" not in item["env"]
        assert item["cwd"] == str(root)


def test_package_cli_independent_from_poise(consumer):
    result = subprocess.run([str(consumer), "-I", "-B", "-c",
                             "import environment_maintenance,sys; assert 'poise' not in sys.modules"],
                            capture_output=True)
    assert result.returncode == 0, (result.stdout, result.stderr)


@pytest.mark.parametrize("old", ["requirements.json", "repairs.json"])
def test_old_directory_without_v2_manifest_never_falls_back(consumer, tmp_path, old):
    fixture = "v1_poison_requirements.json" if old == "requirements.json" else "v1_poison_repairs.json"
    (tmp_path / old).write_bytes((ACTION.parent / fixture).read_bytes())
    for entry in (["-m", "environment_maintenance"],
                  [str(consumer.parent.parent / "bootstrap-layout/bootstrap")]):
        result = subprocess.run([str(consumer), "-I", "-B", *entry, "check", "--catalog-dir", str(tmp_path)], capture_output=True)
        assert result.returncode == 2
        assert result.stdout == b""
        assert result.stderr
        assert not (tmp_path / "calls.jsonl").exists()


def test_multiple_requirements_preserve_declared_order(consumer, catalog):
    root, data = catalog
    other = deepcopy(data["requirements"][0])
    other["id"] = "other"
    for role in ("check", "install", "repair"):
        other[role]["argv"][-2] = "other"
    data["requirements"].append(other)
    result = report(invoke(consumer, catalog), 0, "ready")
    assert [item["id"] for item in result["requirements"]] == ["resource", "other"]
    assert [(item["argv"][-2], item["role"]) for item in calls(root)] == [
        ("resource", "check"), ("resource", "install"), ("resource", "check"), ("other", "check")]


@pytest.mark.parametrize("field,value", [("schema", "wrong"), ("requirement_id", "wrong"), ("action", "repair")])
def test_wrong_child_protocol_identity_never_becomes_ready(consumer, catalog, field, value):
    root, data = catalog
    data["requirements"][0]["check"]["env"] = {"V2_BAD_FIELD": field, "V2_BAD_VALUE": value}
    row = report(invoke(consumer, catalog), 4, "failed")["requirements"][0]
    assert row["initial_check"]["status"] == "protocol_failure"
    assert "response" not in row["initial_check"]
    assert [item["role"] for item in calls(root)] == ["check"]


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_invalid_utf8_child_bytes_fail_before_mutation(consumer, catalog, stream):
    root, data = catalog
    data["requirements"][0]["check"]["argv"][-1] = f"invalid-utf8-{stream}"
    row = report(invoke(consumer, catalog), 4, "failed")["requirements"][0]
    check = row["initial_check"]
    assert check["status"] == "protocol_failure"
    assert check["capture_complete"] is True
    assert check["timed_out"] is False
    assert check["error"].startswith("Invalid action output:")
    assert "response" not in check
    assert [item["role"] for item in calls(root)] == ["check"]
    assert not (root / "resource").exists()
    assert (root / "unrelated").read_text() == "preserve"


@pytest.mark.parametrize("source", ["values", "environment", "cli"])
@pytest.mark.parametrize("name,value", [
    ("count", "wrong"), ("count", True), ("count", 1.5), ("count", None),
    ("enabled", "true"), ("enabled", 1), ("enabled", None),
    ("ratio", "wrong"), ("ratio", True), ("ratio", None)])
def test_invalid_typed_parameters_rejected_before_actions(consumer, catalog, source, name, value):
    root, data = catalog
    data["parameters"] = json.loads((ACTION.parent / "v2_parameters.json").read_text())
    data["parameters"][name]["env"] = "V2_INPUT"
    extra = ()
    env = {}
    if source == "values":
        values = root / "values.json"
        values.write_text(json.dumps({name: value}))
        extra = ("--values", str(values))
    elif source == "environment":
        env["V2_INPUT"] = json.dumps(value)
    else:
        extra = ("--set", name + "=" + json.dumps(value))
    result = invoke(consumer, catalog, extra=extra, env=env)
    assert result.returncode == 2
    assert result.stdout == b""
    assert name.encode() in result.stderr
    assert calls(root) == []
    assert not (root / "resource").exists()


@pytest.mark.parametrize("value", [1, True, None])
def test_non_string_values_parameter_is_rejected(consumer, catalog, value):
    root, data = catalog
    data["parameters"] = json.loads((ACTION.parent / "v2_parameters.json").read_text())
    values = root / "values.json"
    values.write_text(json.dumps({"selected": value}))
    result = invoke(consumer, catalog, extra=("--values", str(values)), env={})
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"selected" in result.stderr
    assert calls(root) == []


@pytest.mark.parametrize("name", ["selected", "count", "enabled", "ratio"])
def test_missing_required_parameter_rejected_before_actions(consumer, catalog, name):
    root, data = catalog
    data["parameters"] = json.loads((ACTION.parent / "v2_parameters.json").read_text())
    data["parameters"][name].pop("default")
    result = invoke(consumer, catalog, env={})
    assert result.returncode == 2
    assert result.stdout == b""
    assert ("Missing parameter: " + name).encode() in result.stderr
    assert calls(root) == []
    assert not (root / "resource").exists()


@pytest.mark.parametrize("case", ["unknown-values", "unknown-cli", "duplicate-cli", "bad-string", "unknown-token"])
def test_invalid_parameter_input_rejected_before_actions(consumer, catalog, case):
    root, data = catalog
    data["parameters"] = json.loads((ACTION.parent / "v2_parameters.json").read_text())
    extra = ()
    if case == "unknown-values":
        values = root / "values.json"
        values.write_text('{"unknown":1}')
        extra = ("--values", str(values))
        diagnostic = b"Unknown values: unknown"
    elif case == "unknown-cli":
        extra = ("--set", "unknown=1")
        diagnostic = b"Unknown or duplicate override: unknown"
    elif case == "duplicate-cli":
        extra = ("--set", "count=1", "--set", "count=2")
        diagnostic = b"Unknown or duplicate override: count"
    elif case == "bad-string":
        extra = ("--set", 'selected="unterminated')
        diagnostic = b"Invalid string override"
    else:
        # Complete expansion precedes even the first otherwise valid action.
        other = deepcopy(data["requirements"][0])
        other["id"] = "other"
        other["repair"]["env"] = {"BOUND": "${unknown}"}
        data["requirements"].append(other)
        diagnostic = b"Unknown placeholder: unknown"
    result = invoke(consumer, catalog, extra=extra, env={})
    assert result.returncode == 2
    assert result.stdout == b""
    assert diagnostic in result.stderr
    assert calls(root) == []
    assert not (root / "resource").exists()


def test_parameter_expansion_is_exactly_one_pass(consumer, catalog):
    root, data = catalog
    data["parameters"] = json.loads((ACTION.parent / "v2_parameters.json").read_text())
    data["parameters"]["selected"]["default"] = "${count}"
    for role in ("check", "install", "repair"):
        data["requirements"][0][role]["env"] = {"BOUND": "${selected}"}
    result = report(invoke(consumer, catalog, env={}), 0, "ready")
    assert result["parameters"]["selected"] == "${count}"
    for item in calls(root):
        assert item["env"]["BOUND"] == "${count}"


def test_accepted_supply_inputs(consumer):
    inputs = json.loads((ACTION.parent / "v2_supply_inputs.json").read_text())
    for item in inputs["files"]:
        source = Path(os.environ[item["environment"]])
        assert source.name == item["name"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"]
    manifest = json.loads(Path(os.environ["AIP_GENERIC_DELIVERY"]).read_text())
    build = json.loads(Path(os.environ["AIP_GENERIC_BUILD"]).read_text())
    assert manifest["build"] == build
    assert manifest["version"] == "0.2.1"
    assert manifest["source_commit"] == "482b11417f8e74d9bcc1d885767bb16837f3284a"
    assert build["exit_code"] == 0
    assert build["source_bundle_sha256"] == hashlib.sha256(Path(os.environ["AIP_GENERIC_SOURCE"]).read_bytes()).hexdigest()
    for stream in ("stdout", "stderr"):
        assert build[stream + "_sha256"] == hashlib.sha256(Path(os.environ["AIP_GENERIC_BUILD_" + stream.upper()]).read_bytes()).hexdigest()


@pytest.mark.parametrize("verb,initial,roles", [
    ("check", "satisfied", ["check"]),
    ("infra", "missing", ["check", "install", "check"]),
    ("deps", "invalid", ["check", "repair", "check"]),
])
def test_selected_repository_bootstrap(consumer, catalog, verb, initial, roles):
    root, _ = catalog
    state(root, initial)
    row = report(invoke(consumer, catalog, verb, bootstrap=True,
                        selected_repository=True), 0, "ready")["requirements"][0]
    assert row["initial_state"] == initial
    assert row["state"] == "satisfied"
    observed = calls(root)
    assert [item["role"] for item in observed] == roles
    assert observed[0] == observed[-1]
    if initial != "satisfied":
        assert row["initial_check"]["invocation"] == row["final_check"]["invocation"]
        assert row["mutation"]["response"]["action"] == roles[1]
    assert (root / "resource").read_text() == "valid"
    assert (root / "unrelated").read_text() == "preserve"


@pytest.mark.parametrize("bootstrap", [False, True])
@pytest.mark.parametrize("value", ["\ud800", "\udc80", "\U00010000"])
def test_opaque_unicode_parameter_has_valid_utf8_json(consumer, catalog, bootstrap, value):
    root, data = catalog
    data["parameters"] = {"opaque": {"type": "string", "default": value}}
    result = invoke(consumer, catalog, "infra" if bootstrap else "run", bootstrap=bootstrap)
    result.stdout.decode("utf-8", errors="strict")
    packet = report(result, 0, "ready")
    assert packet["parameters"]["opaque"] == value
    assert [item["role"] for item in calls(root)] == ["check", "install", "check"]
    assert (root / "unrelated").read_text() == "preserve"


@pytest.mark.parametrize("bootstrap", [False, True])
def test_posix_catalog_path_round_trips_valid_utf8_json(consumer, catalog, bootstrap):
    parent, data = catalog
    raw = os.fsencode(parent) + b"/catalog-\xff"
    os.mkdir(raw)
    root = Path(os.fsdecode(raw))
    (root / "unrelated").write_text("preserve")
    for role in ("check", "install", "repair"):
        data["requirements"][0][role]["argv"][4] = str(root)
        data["requirements"][0][role]["cwd"] = str(root)
    result = invoke(consumer, (root, data), "infra" if bootstrap else "run", bootstrap=bootstrap)
    result.stdout.decode("utf-8", errors="strict")
    row = report(result, 0, "ready")["requirements"][0]
    assert row["initial_check"]["invocation"]["cwd"] == str(root)
    assert [item["role"] for item in calls(root)] == ["check", "install", "check"]
    assert (root / "resource").read_text() == "valid"


@pytest.mark.parametrize("role", ["check", "install", "repair"])
@pytest.mark.parametrize("field", ["argv", "cwd", "env"])
def test_unsupported_os_text_rejected_before_any_action(consumer, catalog, role, field):
    root, data = catalog
    action = data["requirements"][0][role]
    if field == "argv":
        action["argv"].append("\ud800")
    elif field == "cwd":
        action["cwd"] += "\ud800"
    else:
        action["env"] = {"OPAQUE": "\ud800"}
    result = invoke(consumer, catalog, "infra", bootstrap=True)
    assert result.returncode == 2
    assert result.stdout == b""
    assert ("invalid " + field + " encoding").encode() in result.stderr
    assert b"Traceback" not in result.stderr
    assert calls(root) == []
    assert not (root / "resource").exists()
    assert (root / "unrelated").read_text() == "preserve"


@pytest.mark.parametrize("initial,role", [("missing", "install"), ("invalid", "repair")])
@pytest.mark.parametrize("depth", [1100, 12000])
def test_deep_mutation_response_keeps_final_check(consumer, catalog, initial, role, depth):
    root, data = catalog
    state(root, initial)
    action = data["requirements"][0][role]
    action["argv"][-1] = f"deep-response={depth}"
    action["max_output_bytes"] = 32768
    row = report(invoke(consumer, catalog, "infra", bootstrap=True), 4, "failed")["requirements"][0]
    assert row["status"] == "failed"
    assert row["state"] == "satisfied"
    assert row["mutation"]["status"] == "protocol_failure"
    assert row["mutation"]["capture_complete"] is True
    assert row["mutation"]["error"].startswith("Invalid action output:")
    assert row["final_check"]["status"] == "passed"
    assert row["initial_check"]["invocation"] == row["final_check"]["invocation"]
    observed = calls(root)
    assert [item["role"] for item in observed] == ["check", role, "check"]
    assert observed[0] == observed[-1]
    assert (root / "resource").read_text() == "valid"
    assert (root / "unrelated").read_text() == "preserve"


@pytest.mark.parametrize("source", ["catalog", "values", "cli", "environment"])
@pytest.mark.parametrize("depth", [1100, 12000])
def test_deep_input_is_controlled_before_actions(consumer, catalog, source, depth):
    root, data = catalog
    data["parameters"] = {"count": {"type": "integer", "default": 3, "env": "V2_COUNT"}}
    deep = "[" * depth + "0" + "]" * depth
    extra = ()
    env = {}
    if source == "catalog":
        # invoke writes the normal fixture first; the entry reads this raw input below.
        raw = json.dumps(data)[:-1] + ',"extra":' + deep + "}"
        (root / "catalog.json").write_text(raw)
        result = subprocess.run([str(consumer), "-I", "-B",
            str(consumer.parent.parent / "bootstrap-layout/bootstrap"), "infra",
            "--catalog-dir", str(root)], capture_output=True, env={})
    else:
        if source == "values":
            values = root / "values.json"
            values.write_text('{"count":' + deep + "}")
            extra = ("--values", str(values))
        elif source == "cli":
            extra = ("--set", "count=" + deep)
        else:
            env["V2_COUNT"] = deep
        result = invoke(consumer, catalog, "infra", bootstrap=True, extra=extra, env=env)
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr
    assert b"Traceback" not in result.stderr
    assert calls(root) == []
    assert not (root / "resource").exists()
    assert (root / "unrelated").read_text() == "preserve"


@pytest.mark.parametrize("source", ["cli", "environment"])
def test_nested_looking_string_is_opaque(consumer, catalog, source):
    root, data = catalog
    value = "[" * 12000 + "0" + "]" * 12000
    data["parameters"] = {"opaque": {"type": "string", "env": "V2_OPAQUE"}}
    extra = ("--set", "opaque=" + value) if source == "cli" else ()
    env = {"V2_OPAQUE": value} if source == "environment" else {}
    packet = report(invoke(consumer, catalog, "infra", bootstrap=True, extra=extra, env=env), 0, "ready")
    assert packet["parameters"]["opaque"] == value
    assert [item["role"] for item in calls(root)] == ["check", "install", "check"]


@pytest.mark.parametrize("role", ["check", "install", "repair"])
@pytest.mark.parametrize("bound", [10**1000, 0, -1, 3601, True, "1", float("nan"), float("inf")],
                         ids=["huge-integer", "zero", "negative", "above-upper", "boolean", "text", "nan", "infinity"])
def test_invalid_numeric_bound_rejected_before_actions(consumer, catalog, role, bound):
    root, data = catalog
    data["requirements"][0][role]["execution_bound_seconds"] = bound
    result = invoke(consumer, catalog, "infra", bootstrap=True)
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"execution_bound_seconds" in result.stderr
    assert b"Traceback" not in result.stderr
    assert calls(root) == []
    assert not (root / "resource").exists()


@pytest.mark.parametrize("bound", [0.001, 3600])
def test_numeric_bound_valid_controls(consumer, catalog, bound):
    root, data = catalog
    state(root, "satisfied")
    data["requirements"][0]["install"]["execution_bound_seconds"] = bound
    data["requirements"][0]["check"]["execution_bound_seconds"] = 3600
    row = report(invoke(consumer, catalog, "infra", bootstrap=True), 0, "ready")["requirements"][0]
    assert row["state"] == "satisfied"
    assert [item["role"] for item in calls(root)] == ["check"]
    assert (root / "resource").read_text() == "valid"
