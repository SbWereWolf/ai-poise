"""Opt-in acceptance subject: one CLI -> real isolated handoff -> real app MCP.

The assigned reviewer independently checks the received message and raw call log.
No live user Task is executed, accepted or integrated by this test.
"""
import json
import os
from pathlib import Path
import sys

import pytest

from batch.helpers import request, result as stage_result
from conftest import WorkPoise
from poise.application.work import WorkTools
from runtime_services.stage_result_handoff_helpers import configure, fixture, source_state, count
from runtime_services.test_agent_handoff_proxy import FIXTURES, invoke, proxy  # pytest fixture


@pytest.mark.skipif(not os.environ.get("POISE_PROXY_LIVE_MCP_COMMAND"),
                    reason="Explicit live MCP transport and assigned recipient required")
def test_one_public_call_releases_real_fixture_task_and_notifies_assigned_reviewer(proxy, project):
    native = [os.environ[key] for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID") if os.environ.get(key)]
    assert native and len(set(native)) == 1
    actor = native[0]
    receiver = os.environ["POISE_PROXY_LIVE_RECEIVER"]
    output = Path(os.environ["POISE_PROXY_LIVE_OUTPUT"])
    counter = configure(project)
    tools = WorkTools(WorkPoise(project["config_path"], actor))
    context = tools.invoke(request("bootstrap", {"task": project["task"], "decision": None,
                                                "feedback": None, "rework_stage": None}))
    packet = fixture("handoff-packet.json")
    packet["input"]["request_id"] = "proxy-live-acceptance-1"
    packet["input"]["result"] = stage_result(context, "Independent live proxy acceptance result.")
    proxy["packet"]["input"]["handoff"] = packet
    proxy["packet"]["input"]["recipient"] = {"thread_id": receiver, "host_id": "local"}
    proxy["packet"]["input"]["message"] = (FIXTURES / "live-message.txt").read_text()
    proxy["settings"]["project"] = "demo"
    proxy["settings"]["launcher"] = [sys.executable, str(FIXTURES / "native_peer.py"),
                                        str(project["config_path"]), actor, str(proxy["root"])]
    proxy["settings"]["notification"]["command"] = json.loads(os.environ["POISE_PROXY_LIVE_MCP_COMMAND"])
    proxy["environment"] = dict(os.environ)
    proxy["environment"]["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parents[2] / "src"),
                                                          str(Path(__file__).resolve().parents[1]),
                                                          str(Path(__file__).resolve().parents[2])])
    process, delivered = invoke(proxy, expected_exit=0)
    assert delivered["status"] == "delivered" and delivered["notification"]["status"] == "confirmed"
    assert delivered["handoff"]["result"]["release"]["after"] == {
        "actor": actor, "task_id": None, "worktree_task_id": None}
    assert source_state(tools)["claimed_by"] is None and count(counter) == "x"
    texts = delivered["notification"]["response"]["content"]
    acknowledgements = [json.loads(item["text"]) for item in texts if item["type"] == "text"]
    assert {"threadId": receiver} in acknowledgements
    output.write_text(json.dumps({"stdout": process.stdout, "stderr": process.stderr,
                                  "exit_code": process.returncode, "result": delivered,
                                  "settings": proxy["settings"], "fixture_config": str(project["config_path"])},
                                 ensure_ascii=False, indent=2))
