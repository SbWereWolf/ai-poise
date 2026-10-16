"""Retain genuine implementation receipts before Task retirement; no state writes."""
import hashlib
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent
TASK = Path("/home/sbwerewolf/workdata/poise-project-data/erp/standalones/ERP-HARNESS-MODULARITY-001")
CHECKOUT = Path("/home/sbwerewolf/workdata/erp/worktrees/ERP-HARNESS-MODULARITY-001")
RESPONSE = TASK / "runs/a518b997-08bf-415f-9f48-22a7d262c030/response.json"
DEST = BASE / "native-implementation-ccce559b-20261016"
result = json.loads(RESPONSE.read_bytes())
assert result["status"] == "verified" and result["stage_outcome"] == "complete"
assert result["verification_commit"] == "a8ae8509d8c1204805e892bf6b2f24bb37e7b91a"
assert len(result["checks"]) == 10
DEST.mkdir(exist_ok=False)
files = []

def retain(source, relative):
    target = DEST / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    data = source.read_bytes()
    with target.open("xb") as stream:
        stream.write(data)
    files.append({"source": str(source), "path": str(relative),
                  "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})

def retain_tree(source, relative):
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Inspect symlink before archival: " + str(path))
        if path.is_file():
            retain(path, relative / path.relative_to(source))

def check_ids(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("check_id", "source_check_id", "record_path", "recovery_path") and isinstance(item, str):
                found.update(re.findall(r"chk_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{10}", item))
            found.update(check_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(check_ids(item))
    return found

retain(RESPONSE, Path("response.json"))
queue = set()
for receipt in result["checks"]:
    assert receipt["passed"] is True and receipt["actual_exit_code"] == 0
    assert receipt["source_unchanged"] is True and receipt["interpretable"] is True
    assert all(receipt["termination"][key] is True for key in ("child_reaped", "process_group_stopped", "capture_complete"))
    assert len(receipt["systems"]) == 0 and len(receipt["outputs"]) == 0
    for name in ("stdout", "stderr"):
        source = Path(receipt[name])
        assert hashlib.sha256(source.read_bytes()).hexdigest() == receipt[name + "_digest"]
    retain_tree(TASK / "runs" / receipt["id"], Path("native-runs") / receipt["id"])
    text = Path(receipt["stdout"]).read_text().strip()
    if text.startswith("{"):
        queue.update(check_ids(json.loads(text)))

copied = set()
while queue:
    check_id = sorted(queue)[0]
    queue.remove(check_id)
    if check_id in copied:
        continue
    folder = CHECKOUT / ".runtime/checks" / check_id
    if not folder.is_dir():
        raise RuntimeError("Registered evidence dependency missing: " + check_id)
    retain_tree(folder, Path("registered-checks") / check_id)
    copied.add(check_id)
    for record in folder.rglob("record.json"):
        queue.update(check_ids(json.loads(record.read_bytes())) - copied)

manifest = {"kind": "genuine-native-implementation-and-dependencies", "attempt": 3,
            "attempt_id": "ccce559b-dacc-4b33-a75c-a7b9c443c816",
            "verification_commit": result["verification_commit"],
            "verified_tree": result["verified_tree"], "files": files,
            "registered_check_ids": sorted(copied),
            "cache_distinction": "Tooling-fast is a valid declared cache hit; retain its original successful source record, not a new execution claim.",
            "whole_task_acceptance": False, "integration_complete": False}
with (DEST / "archive-manifest.json").open("x") as stream:
    json.dump(manifest, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
print(json.dumps({"status": "retained-native-implementation", "files": len(files),
                  "registered_check_ids": sorted(copied), "directory": str(DEST)}))
