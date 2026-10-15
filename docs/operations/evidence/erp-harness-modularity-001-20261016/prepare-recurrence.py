"""Capture the second terminal interruption; generate a bounded review proposal."""
import hashlib
import json
from pathlib import Path
import sqlite3

BASE = Path(__file__).resolve().parent
DEST = BASE / "recurrence-dc0fc214"
TASK = "ERP-HARNESS-MODULARITY-001"
STATE = Path("/home/sbwerewolf/workdata/poise-project-data/erp")
ORIGINAL = BASE / "operator-recovery.py"
assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == "d49edd08dd7b2d5b4950530c165e5620e2d7482f1a6fc02099c509cd016c53d3"
DEST.mkdir(exist_ok=False)

def save(name, value):
    with (DEST / name).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

with sqlite3.connect((STATE / "tasks/tasks.sqlite").as_uri() + "?mode=ro", uri=True, timeout=0) as db:
    db.execute("PRAGMA query_only=ON")
    task = list(db.execute("SELECT * FROM tasks WHERE id=?", (TASK,)).fetchone())
    execution = list(db.execute("SELECT task_id,data,version FROM task_execution WHERE task_id=?", (TASK,)).fetchone())
    handoffs = [list(row) for row in db.execute("SELECT * FROM handoffs WHERE seq IN (1129,1151) ORDER BY seq")]
    assert task[5] == 161 and execution[2] == 122
    pending = json.loads(execution[1])["pending"]
    assert pending["attempt_id"] == "dc0fc214-3c7c-4c85-806d-bbc1c93c2001"
    assert pending["runs"][0]["termination"]["actual_exit_code"] == 0
    assert pending["runs"][1]["started"] is True and pending["runs"][1]["termination"] is None
    assert all(run["started"] is False for run in pending["runs"][2:])
    save("preimage.json", {"task": task, "execution": execution, "handoffs": handoffs})
    (DEST / "original-execution.json").write_bytes(execution[1].encode())
    (DEST / "native-history").mkdir()
    for table in ("evidence", "submissions", "task_methods"):
        save("native-history/" + table + ".json", {"rows": [list(row) for row in db.execute("SELECT * FROM " + table + " WHERE task_id=? ORDER BY rowid", (TASK,))]})

for name, source in {
    "terminal-response.json": STATE / "codex-hook/observations/cb323df6ccae47dbb85a0610dffd3b8f/response.json",
    "public-readback.json": STATE / "standalones" / TASK / "runs/af8b31ca-021b-42d7-8f39-bd11dc7b81f1/response.json",
}.items():
    (DEST / name).write_bytes(source.read_bytes())
for run in pending["runs"][:2]:
    folder = STATE / "standalones" / TASK / "runs" / run["run_id"]
    for source in folder.rglob("*"):
        if source.is_file():
            target = DEST / "runs" / run["run_id"] / source.relative_to(folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
files = [{"path": str(path.relative_to(DEST)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(DEST.rglob("*")) if path.is_file()]
save("archive-manifest.json", {"files": files, "kind": "second-unknown-original-bytes"})

source = ORIGINAL.read_text()
replacements = {
    'RECOVERY = "modularity-unknown-operator-supersession-20261016"': 'RECOVERY = "modularity-second-unknown-operator-supersession-20261016"',
    'ARCHIVE = SOURCE.parent / "docs/operations/evidence/erp-harness-modularity-001-20261016"': 'ARCHIVE = SOURCE.parent / "docs/operations/evidence/erp-harness-modularity-001-20261016/recurrence-dc0fc214"',
    '!= 159 or before["execution"][2] != 117': '!= 161 or before["execution"][2] != 122',
    'c5637de0-7b3d-4239-bf63-84dd3707289f': 'dc0fc214-3c7c-4c85-806d-bbc1c93c2001',
    '75f5c88a-6ab0-4c09-8dbf-60d4ea80975a': '306b7b7e-b49f-42cb-af0d-87c5c39429c0',
    'after["execution"] = [TASK, packed(execution), 118]': 'after["execution"] = [TASK, packed(execution), 123]',
    'after["execution"][2] = 119': 'after["execution"][2] = 124',
    'four receipts': 'one receipt',
    'Original context Task158 remains stale against159 and later acquisition': 'Removed attempt UUID cannot match a new attempt; actual Task161 owner and identity remain guarded',
    'CAS complete postimage+Task159+foreign hashes; restore three row data preimages, increment execution version to119': 'CAS complete postimage+Task161; restore execution preimage with version124; preserve every handoff',
    '"task_version":159': '"task_version":161',
}
for old, new in replacements.items():
    assert old in source, old
    source = source.replace(old, new)
start = source.index('    for row in after["handoffs"]:')
end = source.index('    with sqlite3.connect(DB.as_uri()', start)
source = source[:start] + '    if before["handoffs"] != after["handoffs"]:\n        raise RuntimeError("Handoffs must remain unchanged")\n' + source[end:]
start = source.index('        for old, new in zip(before["handoffs"], after["handoffs"], strict=True):')
end = source.index('        event_data =', start)
source = source[:start] + source[end:]
# Fingerprint all handoffs, including already recovered historical rows.
source = source.replace('where = " WHERE seq NOT IN (1129,1151)"', 'where = ""')
with (DEST / "operator-recovery.py").open("x", encoding="utf-8") as stream:
    stream.write(source)
print(json.dumps({"status": "captured-no-managed-write", "directory": str(DEST), "files": len(files), "script_sha256": hashlib.sha256(source.encode()).hexdigest()}))
