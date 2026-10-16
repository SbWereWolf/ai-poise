"""Independent resource and subprocess observations for the public v2 CLI."""
import json
import os
from pathlib import Path
import sys
import time


root, role, identifier, mode = sys.argv[1:5]
root = Path(root)
resource = root / "resource"
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps({"role": role, "argv": sys.argv[1:], "cwd": os.getcwd(),
                          "env": dict(os.environ)}) + "\n")
if mode == "timeout":
    (root / "started").write_text(str(os.getpid()))
    time.sleep(2)
    (root / "late-effect").write_text("unexpected")
if role == "check":
    state = ("missing" if not resource.exists() else
             "satisfied" if resource.read_text() == "valid" else "invalid")
    if mode == "unavailable":
        state = "unavailable"
    response = {"schema": "environment-maintenance/action-result/v2",
                "requirement_id": identifier, "action": "check", "state": state}
    if state == "unavailable":
        response.update(cause="Resource is offline", recommendations=["Restore access"])
else:
    if mode not in ("no-fix", "timeout"):
        resource.write_text("valid")
    response = {"schema": "environment-maintenance/action-result/v2",
                "requirement_id": identifier, "action": role, "outcome": "completed"}
output = json.dumps(response, separators=(",", ":")).encode()
if mode.startswith("deep-response="):
    depth = int(mode.split("=")[1])
    output = output[:-1] + b',"extra":' + b"[" * depth + b"0" + b"]" * depth + b"}"
if "V2_BAD_FIELD" in os.environ:
    response[os.environ["V2_BAD_FIELD"]] = os.environ["V2_BAD_VALUE"]
    output = json.dumps(response, separators=(",", ":")).encode()
if mode.startswith("stdout="):
    output += b" " * (int(mode.split("=")[1]) - len(output))
if mode.startswith("stderr=") or mode.startswith("both="):
    count = int(mode.split("=")[1])
    sys.stderr.buffer.write(b"e" * count)
    sys.stderr.buffer.flush()
    if mode.startswith("both="):
        if count == 513:
            # Deliberately separate writes: overflow can kill before stdout.
            time.sleep(.2)
        output += b" " * (count - len(output))
if mode == "malformed":
    output = b"not JSON"
if mode == "invalid-utf8-stdout":
    output += Path(__file__).with_name("invalid_utf8.bin").read_bytes()
if mode == "invalid-utf8-stderr":
    sys.stderr.buffer.write(Path(__file__).with_name("invalid_utf8.bin").read_bytes())
    sys.stderr.buffer.flush()
sys.stdout.buffer.write(output)
sys.stdout.buffer.flush()
if mode == "fail":
    raise SystemExit(7)
