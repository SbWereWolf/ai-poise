"""Installed native producer and isolated fresh-process reader, with full wheel proof."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest

from tests.accounting.agent_metrics_support import FIXTURES, MetricsScene, assert_measurements, literal
from tests.checks_mcp.installed_support import installed
from tests.hook_transport.architecture_support import write
from tests.accounting.agent_metrics_task_support import publish_sprint_source
from tests.accounting.agent_metrics_support import assert_metadata


SOURCE = Path(__file__).resolve().parents[2]
SDK_DRIVER = SOURCE / "tests/context_resume/installed_driver.py"


@pytest.mark.parametrize("connected,metrics", [(False, True), (True, True), (True, False)],
                         ids=["taskless", "published-sprint-task", "existing-sprint-control"])
def test_noneditable_installed_taskless_native_producer_and_fresh_reader(project, tmp_path, connected, metrics):
    if connected:
        publish_sprint_source(project)
    scene = MetricsScene(project, tmp_path, connected=connected)
    if connected:
        scene.definition, scene.events = scene.general.native_definition(all_events=True, task=True)
    else:
        scene.source()
    if metrics:
        scene.configure_metrics()
    environment = {key: value for key, value in scene.general.env.items() if key != "PYTHONPATH"}
    # Reuse the existing owner of actual wheel/build/install/member-byte verification.
    python = installed(SimpleNamespace(root=tmp_path, environment=environment))
    proof_path = tmp_path / "installed/proof.json"
    proof = json.loads(proof_path.read_text())
    assert proof["members"] and proof["origin"]["module"]
    module_root = Path(proof["origin"]["module"]).parent.parent
    settings = json.loads(scene.general.settings_path.read_text())
    settings.update(python=python, source_root=str(module_root))
    write(scene.general.settings_path, settings)
    scene.python, scene.isolated = python, True
    scene.general.env = environment

    if metrics:
        help_argv = [python, "-I", "-B", "-m", "poise", "telemetry", "--help"]
        help_result = subprocess.run(help_argv, capture_output=True, cwd=tmp_path, env=environment)
        scene.record({"kind": "installed-cli-help", "argv": help_argv,
                      "exit_code": help_result.returncode, "stdout": help_result.stdout.decode(),
                      "stderr": help_result.stderr.decode()})
        if help_result.returncode == 2 and b"invalid choice: 'telemetry'" in help_result.stderr:
            raise AssertionError("F16_INSTALLED_METRICS_CLI_ENTRY_MISSING: actual installed argparse rejected telemetry")
        assert help_result.returncode == 0 and not help_result.stderr, scene.calls[-1]
        assert all(option in help_result.stdout for option in (b"--config", b"--checkout", b"--policy", b"--audience"))

    def sdk(action, **fields):
        request = {"action": action, **fields}
        argv = [python, "-I", "-B", str(SDK_DRIVER)]
        result = subprocess.run(argv, input=json.dumps(request).encode(), capture_output=True,
                                cwd=tmp_path, env=environment)
        scene.record({"kind": "installed-sdk", "argv": argv, "request": request,
                      "exit_code": result.returncode, "stdout": result.stdout.decode(),
                      "stderr": result.stderr.decode()})
        assert result.returncode == 0 and not result.stderr, scene.calls[-1]
        return json.loads(result.stdout)

    capability = sdk("capability", definition=scene.definition)
    if capability.get("status") == "rejected" and "Unregistered builtin handler" in capability.get("reason", ""):
        raise AssertionError("F16_INSTALLED_METRICS_NATIVE_CAPABILITY_MISSING: " + capability["reason"])
    assert capability == {"status": "supported"}
    settings_argument = {"settings": str(scene.general.settings_path)}
    revision = sdk("revision", **settings_argument)["revision"]
    request = literal("install-request.json")
    request.update(expected_revision=revision, definition=scene.definition)
    scene.installation = sdk("install", **settings_argument, request=request)
    assert scene.installation["status"] == "installed"
    try:
        scene.native("SessionStart")
        if connected:
            binding_argv = [python, "-I", "-B", str(FIXTURES / "installed_binding.py")]
            binding_read = subprocess.run(binding_argv, input=json.dumps({
                "settings": str(scene.general.settings_path), "session": scene.session}).encode(),
                capture_output=True, cwd=tmp_path, env=environment)
            scene.record({"kind": "installed-binding-readback", "argv": binding_argv,
                          "exit_code": binding_read.returncode, "stdout": binding_read.stdout.decode(),
                          "stderr": binding_read.stderr.decode()})
            assert binding_read.returncode == 0 and not binding_read.stderr
            binding_result = json.loads(binding_read.stdout)
            assert Path(binding_result["poise_source"]).is_relative_to(module_root)
            bootstrap = literal("task-bootstrap.json")
            bootstrap["input"]["task"] = {"id": "T1"}
            acquired = scene.work(bootstrap, binding=binding_result["binding"])
            assert acquired["task"] == "T1" and acquired["sprint_root"]
            scene.source()
        actual = scene.execute_tool("f16-installed-tool", exit_code=7)
        assert actual["exit_code"] == 7 and actual["stdout"] == "А\n".encode()
        scene.native("Stop")
        if not metrics:
            scene.close()
            assert not scene.metrics.exists()
            proof.update(existing_native_sprint_control=True, native_receipt=False,
                         live_host_delivery=False)
            write(proof_path, proof)
            return
        first = scene.wait_query(family="usage-current")
        scene.close()
        second = scene.query(family="usage-current")
        assert_measurements(second, literal("expected-usage-complete.json"))
        assert first["metrics"] == second["metrics"]
        if connected:
            association = scene.query(family="optional-task-policy")
            assert_metadata(association, {"task_id": "T1", "sprint_id": "S"})
            assert_measurements(scene.query(family="usage-current", scope={"kind": "sprint", "id": "S"}),
                                literal("expected-usage-complete.json"))
        readers = [row for row in scene.calls if row["kind"] == "cli"]
        assert len({row["pid"] for row in readers}) >= 2
        assert all(Path(row["poise_source"]).is_relative_to(module_root) for row in readers)
        if not connected:
            assert all(row["sentinel"] == literal("expected-no-task.json") for row in readers)
        proof.update(native_metrics_complete=True, live_host_delivery=False,
                     native_receipt=False, metric_result=second)
        write(proof_path, proof)
    finally:
        scene.close()
