"""Transparent external fixture handler; this is not a dispatcher implementation."""
import json
from pathlib import Path
import socket
import sys


packet = json.load(sys.stdin)
templates = json.loads(Path(__file__).with_name("handler-results.json").read_text(encoding="utf-8"))
options = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
label = options["label"]
folder = Path(options["folder"])
folder.mkdir(parents=True, exist_ok=True)
identifier = packet["invocation_id"]
(folder / (identifier + ".input.json")).write_text(
    json.dumps(packet, ensure_ascii=False), encoding="utf-8"
)
with (folder / "order.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"label": label, "invocation_id": identifier}) + "\n")
barrier = None
if options["barrier"] is not None:
    barrier = socket.create_connection(tuple(options["barrier"]), timeout=15)
    barrier.sendall((label + "\n").encode())
    assert barrier.recv(1) == b"R"
if options["failure"] == "nonzero":
    sys.stderr.write("ACTUAL_WORKER_FAILURE\r\n")
    if barrier is not None:
        barrier.sendall(b"D")
        barrier.close()
    raise SystemExit(7)
if options["failure"] == "invalid-json":
    sys.stdout.write("INVALID_HANDLER_JSON\n")
    raise SystemExit(0)
if packet["mode"] == "async":
    result = templates["async"]
    result.update(invocation_id=identifier, effects={"completed": label})
    if options["failure"] == "response":
        result["response"] = {"systemMessage": "FORBIDDEN_ASYNC_REPLACEMENT"}
    if options["failure"] == "control":
        result["control"] = {"action": "stop", "reason": "FORBIDDEN"}
    if options["failure"] == "wrong-id":
        result["invocation_id"] = "00000000-0000-0000-0000-000000000001"
else:
    previous = packet["response"].get("systemMessage", "seed")
    result = templates["sync"]
    result.update(invocation_id=identifier, response={"systemMessage": previous + "|" + label},
                  control={"action": options["action"],
                           "reason": None if options["action"] == "continue" else "fixture stop"})
(folder / (identifier + ".result.json")).write_text(
    json.dumps(result, ensure_ascii=False), encoding="utf-8"
)
sys.stderr.write("fixture stderr: " + label + "\r\n")
sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
sys.stdout.flush()
sys.stderr.flush()
if barrier is not None:
    barrier.sendall(b"D")
    barrier.close()
