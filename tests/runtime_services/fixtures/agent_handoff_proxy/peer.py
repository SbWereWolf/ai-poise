"""Test-owned process/protocol peers, independent of proxy production code."""
import json
import os
from pathlib import Path
import signal
import sys
import time


mode, directory = sys.argv[1:3]
root = Path(directory)
scenario = json.loads((root / "scenario.json").read_text())


def record(event, **values):
    with (root / "trace.jsonl").open("a") as stream:
        stream.write(json.dumps({"event": event, **values}) + "\n")


def reply(identity, result):
    print(json.dumps({"jsonrpc": "2.0", "id": identity, "result": result}), flush=True)


if mode == "handoff":
    packet = json.load(sys.stdin)
    record("handoff", packet=packet, arguments=sys.argv[3:])
    marker = root / "native-effect.txt"
    if not marker.exists():
        marker.write_text("one native business result")
        record("native_effect")
    receipt = root / "native-response.json"
    receipt.write_text(json.dumps(scenario["handoff"], ensure_ascii=False))
    if scenario.get("handoff_delay"):
        time.sleep(scenario["handoff_delay"])
    if scenario.get("lose_handoff_ack") and not (root / "ack-lost").exists():
        (root / "ack-lost").touch()
        print("native result already committed; response lost", flush=True)
        sys.exit(1)
    if scenario.get("invalid_receipt"):
        receipt.write_text("{unclosed")
    if scenario.get("missing_receipt"):
        receipt.unlink()
    print(scenario.get("native_stderr", ""), file=sys.stderr)
    print(json.dumps({"status": "handoff_completed", "response_path": str(receipt),
                      "diagnostic": scenario.get("native_stdout", "")}))
    sys.exit(scenario.get("handoff_exit", 0))

assert mode == "mcp"
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        reply(request["id"], {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "independent-notifier", "version": "1"}})
    elif method == "tools/list":
        tools = [] if scenario.get("no_send_tool") else [{
            "name": "send_message_to_thread", "description": "Test peer",
            "inputSchema": {"type": "object", "properties": {"threadId": {"type": "string"},
                            "prompt": {"type": "string"}, "hostId": {"type": "string"}},
                            "required": ["threadId", "prompt"]}}]
        reply(request["id"], {"tools": tools})
    elif method == "tools/call":
        assert request["params"]["name"] == "send_message_to_thread"
        record("send", params=request["params"])
        outcome = scenario.get("notification", "success")
        if outcome == "is_error":
            reply(request["id"], {"isError": True, "content": [{"type": "text", "text": "Delivery state unavailable"}]})
        elif outcome == "disconnect":
            sys.exit(0)
        elif outcome == "kill_proxy":
            os.kill(os.getppid(), signal.SIGKILL)
            sys.exit(0)
        elif outcome == "malformed_json":
            print("{bad-json", flush=True)
            sys.exit(0)
        elif outcome == "rpc_error":
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                              "error": {"code": -32603, "message": "Outcome unconfirmed"}}), flush=True)
        elif outcome == "malformed_result":
            reply(request["id"], {"content": "not-an-array", "isError": False})
        else:
            if outcome == "slow_success":
                time.sleep(0.3)
            thread = request["params"]["arguments"]["threadId"]
            ack = {} if outcome == "missing_recipient" else {"threadId": "other" if outcome == "wrong_recipient" else thread}
            reply(request["id"], {"isError": False, "content": [{"type": "text", "text": json.dumps(ack)}]})
