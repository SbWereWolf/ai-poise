"""Actual configured repository Bootstrap with the immutable selected v2 wheel."""

import importlib.util
import json
import os
from pathlib import Path
import time

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "aip_bootstrap_test_support",
    REPO / "tests/maintenance/fixtures/generic_v2_support.py")
assert _spec is not None and _spec.loader is not None
_support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_support)
consumer, catalog = _support.consumer, _support.catalog
bootstrap, outer, underlying = _support.bootstrap, _support.outer, _support.underlying
calls, state = _support.calls, _support.state


def snapshot(root):
    return {name: ((root / name).read_bytes(),
                   (root / name).stat().st_mode)
    if (root / name).exists() else None for name in
            ("resource", "unrelated")}


def input_failure(result, root, before):
    packet = outer(result, 4, "error", ["initial"], [])
    observed = packet["observations"][0]
    assert observed["exit_code"] == 2
    assert observed["stdout"] == ""
    assert observed["stderr"] and "Traceback" not in observed[
        "stderr"]
    assert calls(root) == []
    assert snapshot(root) == before
    return observed


@pytest.mark.parametrize("operation,initial,roles", [
    ("check", "satisfied", ["check"]),
    ("infra", "missing",
     ["check", "check", "install", "check", "check"]),
    ("deps", "invalid",
     ["check", "check", "repair", "check", "check"]),
])
def test_configured_bootstrap_state_actions_and_observations(consumer,
                                                             catalog,
                                                             operation,
                                                             initial,
                                                             roles):
    root, _ = catalog
    state(root, initial)
    stages = ["initial"] if initial == "satisfied" else ["initial",
                                                         "repair",
                                                         "verification"]
    actions = [] if initial == "satisfied" else ["repair", "verify"]
    packet = outer(bootstrap(consumer, catalog, operation), 0,
                   "ready", stages, actions)
    first = underlying(packet["observations"][0],
                       0 if initial == "satisfied" else 2,
                       "ready" if initial == "satisfied" else "action_required")
    assert first["requirements"][0]["initial_state"] == initial
    if initial != "satisfied":
        repaired = underlying(packet["observations"][1], 0, "ready")[
            "requirements"][0]
        assert repaired["initial_state"] == initial and repaired[
            "state"] == "satisfied"
        assert repaired["initial_check"]["invocation"] == \
               repaired["final_check"]["invocation"]
        assert repaired["mutation"]["response"]["action"] == roles[2]
        assert underlying(packet["observations"][2], 0, "ready")[
                   "requirements"][0]["state"] == "satisfied"
    assert [row["role"] for row in calls(root)] == roles
    assert (root / "resource").read_text() == "valid"
    assert (root / "unrelated").read_text() == "preserve"
    repeated = outer(bootstrap(consumer, catalog, operation), 0,
                     "ready", ["initial"], [])
    assert underlying(repeated["observations"][0], 0, "ready")[
               "requirements"][0]["state"] == "satisfied"
    assert [row["role"] for row in calls(root)] == roles + ["check"]


@pytest.mark.parametrize("initial",
                         ["satisfied", "missing", "invalid",
                          "unavailable"])
@pytest.mark.parametrize("operation,extra",
                         [("check", ()), ("infra", ("--dry-run",)),
                          ("deps", ("--dry-run",))])
def test_configured_bootstrap_readonly_preserves_resources(consumer,
                                                           catalog,
                                                           initial,
                                                           operation,
                                                           extra):
    root, data = catalog
    state(root, "invalid" if initial == "unavailable" else initial)
    if initial == "unavailable":
        data["requirements"][0]["check"]["argv"][-1] = "unavailable"
    before = snapshot(root)
    ready = initial == "satisfied"
    packet = outer(
        bootstrap(consumer, catalog, operation, extra=extra),
        0 if ready else 4, "ready" if ready else "error", ["initial"],
        [])
    code, status = (0, "ready") if ready else (
        (3, "unavailable") if initial == "unavailable" else (2,
                                                             "action_required"))
    row = underlying(packet["observations"][0], code, status)[
        "requirements"][0]
    assert row["initial_state"] == initial and "mutation" not in row
    assert [item["role"] for item in calls(root)] == ["check"]
    assert snapshot(root) == before


def test_configured_bootstrap_unavailable_preserves_guidance(consumer,
                                                             catalog):
    root, data = catalog
    state(root, "invalid")
    data["requirements"][0]["check"]["argv"][-1] = "unavailable"
    before = snapshot(root)
    packet = outer(bootstrap(consumer, catalog), 4, "error",
                   ["initial"], [])
    row = underlying(packet["observations"][0], 3, "unavailable")[
        "requirements"][0]
    assert row["cause"] == "Resource is offline"
    assert row["recommendations"] == ["Restore access"]
    assert [item["role"] for item in calls(root)] == ["check"]
    assert snapshot(root) == before


@pytest.mark.parametrize("initial,role", [("missing", "install"),
                                          ("invalid", "repair")])
@pytest.mark.parametrize("mode",
                         ["fail", "no-fix", "malformed", "stdout=513",
                          "stderr=513", "timeout",
                          "deep-response=1100",
                          "deep-response=12000"])
def test_configured_bootstrap_failed_repair_keeps_original_check(
        consumer, catalog, initial, role, mode):
    root, data = catalog
    state(root, initial)
    action = data["requirements"][0][role]
    action["argv"][-1] = mode
    action["max_output_bytes"] = 32768 if mode.startswith(
        "deep-response=") else 512
    if mode == "timeout":
        action["execution_bound_seconds"] = .5
    packet = outer(bootstrap(consumer, catalog), 4, "error",
                   ["initial", "repair", "verification"],
                   ["repair", "verify"])
    assert \
    underlying(packet["observations"][0], 2, "action_required")[
        "requirements"][0]["state"] == initial
    row = underlying(packet["observations"][1], 4, "failed")[
        "requirements"][0]
    assert row["status"] == "failed"
    assert row["initial_check"]["invocation"] == row["final_check"][
        "invocation"]
    assert row["final_check"]["status"] == "passed"
    assert row["mutation"]["status"] == (
        "passed" if mode == "no-fix" else "protocol_failure")
    final_state = initial if mode in ("no-fix",
                                      "timeout") else "satisfied"
    assert row["state"] == final_state
    verified = underlying(packet["observations"][2],
                          0 if final_state == "satisfied" else 2,
                          "ready" if final_state == "satisfied" else "action_required")
    assert verified["requirements"][0]["state"] == final_state
    observed = calls(root)
    assert [item["role"] for item in observed] == ["check", "check",
                                                   role, "check",
                                                   "check"]
    assert observed[1] == observed[3]
    assert (root / "unrelated").read_text() == "preserve"
    if mode.startswith("deep-response="):
        assert row["mutation"]["capture_complete"] is True
        assert row["mutation"]["error"].startswith(
            "Invalid action output:")
    if mode == "timeout":
        assert row["mutation"]["timed_out"] is True
        pid = int((root / "started").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        time.sleep(2)
        assert not (root / "late-effect").exists()


@pytest.mark.parametrize("value", ["\ud800", "\udc80", "\U00010000"])
def test_configured_bootstrap_regression_boundaries_preserve_diagnostics(
        consumer, catalog, value):
    root, data = catalog
    data["parameters"] = {
        "opaque": {"type": "string", "default": value}}
    packet = outer(bootstrap(consumer, catalog), 0, "ready",
                   ["initial", "repair", "verification"],
                   ["repair", "verify"])
    for observed, code, status in zip(packet["observations"],
                                      (2, 0, 0),
                                      ("action_required", "ready",
                                       "ready")):
        assert underlying(observed, code, status)["parameters"][
                   "opaque"] == value
    assert [row["role"] for row in calls(root)] == ["check", "check",
                                                    "install",
                                                    "check", "check"]


def test_configured_bootstrap_posix_catalog_path_round_trips(consumer,
                                                             catalog):
    parent, data = catalog
    raw = os.fsencode(parent) + b"/catalog-\xff"
    os.mkdir(raw)
    root = Path(os.fsdecode(raw))
    (root / "unrelated").write_text("preserve")
    for role in ("check", "install", "repair"):
        data["requirements"][0][role]["argv"][4] = str(root)
        data["requirements"][0][role]["cwd"] = str(root)
    packet = outer(bootstrap(consumer, (root, data)), 0, "ready",
                   ["initial", "repair", "verification"],
                   ["repair", "verify"])
    row = \
    underlying(packet["observations"][1], 0, "ready")["requirements"][
        0]
    assert row["initial_check"]["invocation"]["cwd"] == str(root)
    assert [item["role"] for item in calls(root)] == ["check",
                                                      "check",
                                                      "install",
                                                      "check",
                                                      "check"]
    assert (root / "resource").read_text() == "valid"


@pytest.mark.parametrize("role", ["check", "install", "repair"])
@pytest.mark.parametrize("field", ["argv", "cwd", "env"])
@pytest.mark.parametrize("expanded", [False, True])
def test_configured_bootstrap_invalid_os_text_is_controlled(consumer,
                                                            catalog,
                                                            role,
                                                            field,
                                                            expanded):
    root, data = catalog
    bad = "${opaque}" if expanded else "\ud800"
    if expanded:
        data["parameters"] = {
            "opaque": {"type": "string", "default": "\ud800"}}
    action = data["requirements"][0][role]
    if field == "argv":
        action["argv"].append(bad)
    elif field == "cwd":
        action["cwd"] = bad
    else:
        action["env"] = {"OPAQUE": bad}
    before = snapshot(root)
    observed = input_failure(bootstrap(consumer, catalog), root,
                             before)
    assert "invalid " + field + " encoding" in observed["stderr"]


@pytest.mark.parametrize("role,initial", [("check", "satisfied"),
                                          ("install", "missing"),
                                          ("repair", "invalid")])
@pytest.mark.parametrize("field", ["argv", "cwd", "env"])
@pytest.mark.parametrize("value", ["\udc80", "\U00010000"])
def test_configured_bootstrap_supported_expanded_os_values(consumer,
                                                           catalog,
                                                           role,
                                                           initial,
                                                           field,
                                                           value):
    root, data = catalog
    state(root, initial)
    action = data["requirements"][0][role]
    if field == "argv":
        action["argv"].append("${opaque}")
    elif field == "cwd":
        directory = root / ("working-" + value)
        directory.mkdir()
        value = str(directory)
        action["cwd"] = "${opaque}"
    else:
        action["env"] = {"OPAQUE": "${opaque}"}
    data["parameters"] = {
        "opaque": {"type": "string", "default": value}}
    correcting = initial != "satisfied"
    packet = outer(bootstrap(consumer, catalog), 0, "ready",
                   ["initial", "repair",
                    "verification"] if correcting else ["initial"],
                   ["repair", "verify"] if correcting else [])
    observed = packet["observations"][1 if correcting else 0]
    assert underlying(observed, 0, "ready")["parameters"][
               "opaque"] == value
    selected = next(row for row in calls(root) if row["role"] == role)
    if field == "argv":
        assert selected["argv"][-1] == value
    elif field == "cwd":
        assert selected["cwd"] == value
    else:
        assert selected["env"]["OPAQUE"] == value
    assert [row["role"] for row in calls(root)] == (
        ["check", "check", role, "check",
         "check"] if correcting else ["check"])


@pytest.mark.parametrize("source",
                         ["catalog", "values", "cli", "environment"])
@pytest.mark.parametrize("depth", [1100, 12000])
def test_configured_bootstrap_deep_input_is_controlled(consumer,
                                                       catalog,
                                                       source, depth):
    root, data = catalog
    data["parameters"] = {
        "count": {"type": "integer", "default": 3, "env": "V2_COUNT"}}
    deep = "[" * depth + "0" + "]" * depth
    extra, env, write = (), {}, True
    if source == "catalog":
        (root / "catalog.json").write_text(
            json.dumps(data)[:-1] + ',"extra":' + deep + "}")
        write = False
    elif source == "values":
        path = root / "values.json"
        path.write_text('{"count":' + deep + "}")
        extra = ("--values", str(path))
    elif source == "cli":
        extra = ("--set", "count=" + deep)
    else:
        env["V2_COUNT"] = deep
    before = snapshot(root)
    input_failure(bootstrap(consumer, catalog, extra=extra, env=env,
                            write=write), root, before)


@pytest.mark.parametrize("role", ["check", "install", "repair"])
def test_configured_bootstrap_huge_numeric_bound_is_controlled(
        consumer, catalog, role):
    root, data = catalog
    data["requirements"][0][role][
        "execution_bound_seconds"] = 10 ** 1000
    before = snapshot(root)
    observed = input_failure(bootstrap(consumer, catalog), root,
                             before)
    assert "execution_bound_seconds" in observed["stderr"]


@pytest.mark.parametrize("legacy", ["--repairs", "apply"])
def test_configured_bootstrap_rejects_retired_inputs_before_actions(
        consumer, catalog, legacy):
    root, _ = catalog
    result = bootstrap(consumer, catalog,
                       "apply" if legacy == "apply" else "infra",
                       extra=("--repairs",
                              str(root / "repairs.json")) if legacy == "--repairs" else ())
    assert result.returncode == 2 and result.stdout == b"" and result.stderr
    assert calls(root) == []
    assert not (root / "resource").exists()
