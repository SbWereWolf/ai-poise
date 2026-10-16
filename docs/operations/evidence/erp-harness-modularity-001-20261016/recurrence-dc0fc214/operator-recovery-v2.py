"""Single-incident operator recovery. Does not fabricate a native receipt."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

TASK = "ERP-HARNESS-MODULARITY-001"
ACTOR = "01a138d3-c841-7c01-a4c2-ca6aaccfffc5"
RECOVERY = "modularity-second-unknown-operator-supersession-20261016"
ROOT = Path("/home/sbwerewolf/workdata/erp/worktrees") / TASK
STATE = Path("/home/sbwerewolf/workdata/poise-project-data/erp")
CONFIG = Path("/home/sbwerewolf/workdata/poise-op/projects/erp/config/project.json")
SOURCE = Path("/home/sbwerewolf/workdata/poise-op/app/src")
ARCHIVE = SOURCE.parent / "docs/operations/evidence/erp-harness-modularity-001-20261016/recurrence-dc0fc214"
DB = STATE / "tasks/tasks.sqlite"
PLAN = ARCHIVE / "operator-plan-v2.json"
APPLIED = ARCHIVE / "operator-applied-v2.json"
EVENT = "manual.operator_unknown_attempt_superseded"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_new(path, value):
    with path.open("xb") as stream:
        stream.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())


def read(path):
    return json.loads(path.read_bytes())


def verify_archive():
    manifest = read(ARCHIVE / "archive-manifest.json")
    for item in manifest["files"]:
        if digest((ARCHIVE / item["path"]).read_bytes()) != item["sha256"]:
            raise RuntimeError("Durable archive changed: " + item["path"])
        if item["path"].startswith("accepted-source/"):
            source = ROOT / item["path"].removeprefix("accepted-source/")
            if digest(source.read_bytes()) != item["sha256"]:
                raise RuntimeError("Accepted source changed: " + str(source))


def verify_native_history(db):
    for table in ("evidence", "submissions", "task_methods"):
        old = read(ARCHIVE / "native-history" / (table + ".json"))
        rows = [list(row) for row in db.execute("SELECT * FROM " + table + " WHERE task_id=? ORDER BY rowid", (TASK,))]
        if rows != old["rows"]:
            raise RuntimeError("Original native history changed: " + table)


def current_rows(db):
    return {
        "task": list(db.execute("SELECT * FROM tasks WHERE id=?", (TASK,)).fetchone()),
        "execution": list(db.execute("SELECT task_id,data,version FROM task_execution WHERE task_id=?", (TASK,)).fetchone()),
        "handoffs": [list(row) for row in db.execute("SELECT * FROM handoffs WHERE seq IN (1129,1151) ORDER BY seq")],
    }


def quiescence(expected_exclusions=None):
    ancestors = set()
    pid = os.getpid()
    while pid and pid not in ancestors:
        ancestors.add(pid)
        status = (Path("/proc") / str(pid) / "status").read_text()
        pid = int(next(line.split()[1] for line in status.splitlines() if line.startswith("PPid:")))
    matches = []
    excluded_managers = []
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit() or int(directory.name) in ancestors:
            continue
        try:
            if directory.stat().st_uid != os.getuid():
                continue
            command = (directory / "cmdline").read_bytes()
            try:
                cwd = Path(os.readlink(directory / "cwd"))
            except PermissionError:
                status = (directory / "status").read_text()
                observed = {
                    1257: (b"/usr/lib/systemd/systemd\x00--user\x00", "systemd", 1),
                    1261: (b"(sd-pam)\x00", "(sd-pam)", 1257),
                }
                expected = observed.get(int(directory.name))
                if expected is not None and os.getuid() == 1000 and command == expected[0] and f"PPid:\t{expected[2]}\n" in status and f"Name:\t{expected[1]}\n" in status:
                    start_ticks = (directory / "stat").read_text().rsplit(")", 1)[1].split()[19]
                    excluded_managers.append({"pid": int(directory.name), "uid": 1000,
                                              "ppid": expected[2], "name": expected[1], "start_ticks": start_ticks,
                                              "command_sha256": digest(command),
                                              "reason": "Individually verified pre-existing user-manager/PAM service, not the Task Bash/Python runner; all children independently enumerated"})
                    continue
                raise
            scoped = cwd == ROOT or ROOT in cwd.parents or str(ROOT).encode() in command
            ambiguous = any(marker in command for marker in (b"harness-runner-contracts", b"ai-assistant/tests/checks/run-check.py", b"ai-assistant/tests/checks/fixtures/command_fixture.py"))
            if scoped or ambiguous:
                matches.append({"pid": int(directory.name), "task_path": scoped,
                                "runner_contract_command": ambiguous, "command_sha256": digest(command)})
        except (FileNotFoundError, ProcessLookupError):
            continue
    if matches:
        raise RuntimeError("Present Task/ambiguous runner-contract writers require inspection: " + packed(matches))
    excluded_managers.sort(key=lambda item: item["pid"])
    if expected_exclusions is not None and excluded_managers != expected_exclusions:
        raise RuntimeError("Observed excluded process identities changed; inspect before correction")
    return {"at": datetime.now(timezone.utc).isoformat(), "matched_processes": [], "excluded_managers": excluded_managers,
            "scope": "same-uid Task checkout cwd/arguments, runner-contract profile and exact suite/fixture argv markers; current invocation ancestors excluded",
            "historical_termination_proof": False,
            "effects": "Actual method invokes run_check.py --command-id harness-runner-contracts in ROOT. Its configured profile invokes tests/checks/run-check.py. python-runtime.sh sets TMPDIR=ROOT/.runtime/tmp/python; temp_root creates tempfile roots below it, with local .runtime evidence/cache and ai-assistant/checks/data. Fixture commands use cwd=temp_root; spawned and detached python -c children inherit that cwd, including output holders, so ROOT-descendant cwd inspection covers them even without a Task path in argv. Outer runner evidence and adoption ownership remain below ROOT/.runtime. Current absence does not infer historical termination, capture or UNKNOWN outcome."}


def foreign_fingerprints(db):
    result = {}
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + table.replace('"', '""') + '"'
        where, args = "", ()
        if table == "task_execution":
            where, args = " WHERE task_id<>?", (TASK,)
        elif table == "handoffs":
            where = ""
        elif table == "journal":
            where, args = " WHERE NOT ((event IS ? OR event IS ?) AND task_id IS ? AND json_extract(data,'$.recovery_id') IS ?)", (EVENT, EVENT+"_rolled_back", TASK, RECOVERY)
        elif table == "sqlite_sequence":
            where = " WHERE name<>'journal'"
        fingerprint = hashlib.sha256()
        count = 0
        for row in db.execute("SELECT * FROM " + quoted + where + " ORDER BY rowid", args):
            fingerprint.update((packed(row) + "\n").encode())
            count += 1
        result[table] = {"rows": count, "sha256": fingerprint.hexdigest()}
    return result


def owner_hashes():
    names = ("poise/application/check_attempts.py", "poise/infrastructure/handoff.py",
             "poise/infrastructure/project_resources.py", "poise/infrastructure/task_delivery.py",
             "poise/infrastructure/locking.py", "poise/infrastructure/sqlite/handoff.py")
    return {name: digest((SOURCE / name).read_bytes()) for name in names}


def plan():
    verify_archive()
    saved = read(ARCHIVE / "preimage.json")
    before = {"task": saved["task"], "execution": saved["execution"],
              "handoffs": [row for row in saved["handoffs"] if row[0] in (1129,1151)]}
    if before["task"][5] != 161 or before["execution"][2] != 122:
        raise RuntimeError("Unexpected preimage version")
    execution = json.loads(before["execution"][1])
    if execution["pending"]["attempt_id"] != "dc0fc214-3c7c-4c85-806d-bbc1c93c2001":
        raise RuntimeError("Unexpected attempt")
    after = json.loads(packed(before))
    execution["pending"] = None
    after["execution"] = [TASK, packed(execution), 123]
    if before["handoffs"] != after["handoffs"]:
        raise RuntimeError("Handoffs must remain unchanged")
    with sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True, timeout=0) as db:
        db.execute("PRAGMA query_only=ON")
        if current_rows(db) != before:
            raise RuntimeError("Current rows differ from archived complete preimage")
        verify_native_history(db)
    result = {"recovery_id": RECOVERY, "kind": "operator-supersession-not-native-restart",
              "before": before, "after": after, "quiescence_preparation": quiescence(),
              "config_sha256": digest(CONFIG.read_bytes()), "owner_sha256": owner_hashes(),
              "script_sha256": digest(Path(__file__).read_bytes()),
              "archive_manifest_sha256": digest((ARCHIVE / "archive-manifest.json").read_bytes()),
              "outcome": "Original unknown run remains UNKNOWN; one receipt and all history are immutable",
              "late_effect_guard": "Removed attempt UUID cannot match a new attempt; actual Task161 owner and identity remain guarded; start_run/record_termination/finish validate owned task and exact attempt identity. Removed active reference cannot match original ID. A new genuine attempt must receive a new UUID.",
              "rollback": "Only before any public progression: CAS complete postimage+Task161; restore execution preimage with version124; preserve every handoff, append truthful rollback event. Never restore whole DB or delete history."}
    write_new(PLAN, result)
    print(packed({"status": "plan-only-no-database-write", "plan": str(PLAN), "sha256": digest(PLAN.read_bytes())}))


def apply(approved, rollback=False):
    if digest(PLAN.read_bytes()) != approved:
        raise RuntimeError("Approved plan hash mismatch")
    proposal = read(PLAN)
    if proposal["script_sha256"] != digest(Path(__file__).read_bytes()):
        raise RuntimeError("Reviewed recovery script changed")
    if proposal["config_sha256"] != digest(CONFIG.read_bytes()) or proposal["owner_sha256"] != owner_hashes():
        raise RuntimeError("Configured recovery owner changed")
    if proposal["archive_manifest_sha256"] != digest((ARCHIVE / "archive-manifest.json").read_bytes()):
        raise RuntimeError("Durable archive manifest changed")
    output = ARCHIVE / "operator-rolled-back.json" if rollback else APPLIED
    if output.exists():
        raise RuntimeError("Operator record already exists; inspect effects, never repeat blindly")
    before, after = proposal["before"], proposal["after"]
    if rollback:
        applied = read(APPLIED)
        if applied["status"] != "operator-correction-committed" or applied["plan_sha256"] != approved:
            raise RuntimeError("No matching committed operator correction to roll back")
        before, after = proposal["after"], json.loads(packed(proposal["before"]))
        after["execution"][2] = 124
    verify_archive()
    sys.path.insert(0, str(SOURCE))
    from poise.infrastructure.project_resources import ProjectRuntimeFence
    from poise.infrastructure.locking import exclusive_lock
    cfg = read(CONFIG)
    with ExitStack() as stack:
        stack.enter_context(ProjectRuntimeFence(CONFIG, STATE).locked())
        stack.enter_context(exclusive_lock(STATE / cfg["batch"]["artifact_lock"], cfg["limits"]["lock_seconds"], cfg["limits"]["lock_poll_seconds"], existing=True))
        stack.enter_context(exclusive_lock(STATE / cfg["paths"]["lock"], cfg["limits"]["lock_seconds"], cfg["limits"]["lock_poll_seconds"], existing=True))
        live = quiescence(proposal["quiescence_preparation"]["excluded_managers"])
        db = stack.enter_context(sqlite3.connect(DB, timeout=0))
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        if current_rows(db) != before:
            raise RuntimeError("CAS preimage mismatch; no changes")
        verify_native_history(db)
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError("Storage integrity failed before correction")
        fk_before = db.execute("PRAGMA foreign_key_check").fetchall()
        foreign_before = foreign_fingerprints(db)
        journal_sequence_before = db.execute("SELECT seq FROM sqlite_sequence WHERE name='journal'").fetchone()[0]
        live["confirmed_immediately_before_write"] = quiescence(live["excluded_managers"])["at"]
        changed = db.execute("UPDATE task_execution SET data=?,version=? WHERE task_id=? AND version=? AND data=?",
                             (after["execution"][1], after["execution"][2], TASK, before["execution"][2], before["execution"][1])).rowcount
        if changed != 1:
            raise RuntimeError("Execution CAS failed")
        event_data = {"recovery_id": RECOVERY, "operator": ACTOR, "plan_sha256": approved,
                      "archive": str(ARCHIVE), "original_attempt": "dc0fc214-3c7c-4c85-806d-bbc1c93c2001",
                      "original_unknown_run": "306b7b7e-b49f-42cb-af0d-87c5c39429c0",
                      "outcome": "UNKNOWN preserved; operator correction rolled back" if rollback else "UNKNOWN preserved; active reference superseded by operator decision",
                      "quiescence": live, "native_acceptance": False}
        seq = db.execute("INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)",
                         (datetime.now(timezone.utc).isoformat(),ACTOR,TASK,EVENT+"_rolled_back" if rollback else EVENT,packed(event_data))).lastrowid
        if current_rows(db) != after or foreign_fingerprints(db) != foreign_before:
            raise RuntimeError("Postimage/foreign-row invariant failed")
        if seq != journal_sequence_before + 1 or db.execute("SELECT seq FROM sqlite_sequence WHERE name='journal'").fetchone()[0] != seq:
            raise RuntimeError("Unexpected journal sequence change")
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchall() != fk_before:
            raise RuntimeError("Postcorrection integrity failed")
        if proposal["owner_sha256"] != owner_hashes() or proposal["config_sha256"] != digest(CONFIG.read_bytes()):
            raise RuntimeError("Owner/config changed during correction")
        receipt = {"recovery_id": RECOVERY, "status": "operator-correction-prepared", "journal_seq": seq,
                   "plan_sha256": approved, "before": before, "after": after,
                   "foreign_fingerprints": foreign_before, "foreign_key_check": fk_before,
                   "quick_check": "ok", "quiescence": live, "native_acceptance": False}
        write_new(ARCHIVE / ("operator-rollback-precommit.json" if rollback else "operator-precommit.json"), receipt)
        db.commit()
        receipt["status"] = "operator-correction-rolled-back" if rollback else "operator-correction-committed"
        write_new(output, receipt)
        print(packed({"status": receipt["status"], "journal_seq": seq, "task_version":161,
                      "execution_version":after["execution"][2],"original_outcome":"UNKNOWN","native_acceptance":False}))


parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=("plan", "apply", "rollback"))
parser.add_argument("--approved-plan-sha")
args = parser.parse_args()
if args.mode == "plan":
    plan()
else:
    if not args.approved_plan_sha:
        parser.error("apply requires the independently reviewed plan hash")
    apply(args.approved_plan_sha, rollback=args.mode == "rollback")
