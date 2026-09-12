import json
import sqlite3
from pathlib import Path
import pytest
from conftest import add_test, fill
from conftest import Poise
from poise.common import PoiseError


def bootstrap(project):
    h = Poise(project["config_path"], "S1")
    return h, h.bootstrap(task_file=project["task_path"])


def test_runtime_uses_library_and_sections_survive_cleanup(project):
    h,b = bootstrap(project)
    add_test(b["worktree"]); fill(b, "Первый содержательный результат")
    h.verify()
    section = h.task_queries.section("T1", "tests", "report", None)
    assert section["content"] == "Первый содержательный результат"
    assert section["state"] == "populated"
    assert not Path(b["runtime_root"]).exists()
    rework = h.bootstrap(decision="rework", feedback="Уточни описание")
    fill(rework, "Второй результат"); h.verify()
    latest = h.task_queries.section("T1", "tests", "report", None)
    old = h.task_queries.section("T1", "tests", "report", section["submission"])
    assert latest["content"] == "Второй результат"
    assert old == section
    assert h.show()["submission_count"] == 2


def test_same_payload_fixed_code_no_duplicate_section_rows(project):
    h,b = bootstrap(project); add_test(b["worktree"]); fill(b)
    Path(b["worktree"], "tests/test_double.py").write_text("import missing_module_for_red\n")
    assert h.verify()["status"] == "checks_failed"
    first = h.task_queries.section("T1", "tests", "report", None)
    add_test(b["worktree"])
    assert h.verify()["status"] == "verified"
    assert h.task_queries.section("T1", "tests", "report", None) == first


def test_A_B_A_are_distinct_submissions_and_latest_is_A(project):
    h,b = bootstrap(project)
    def payload(text):
        return {"sections":{"report":text},"artifact_paths":[],"commit_message":"test: result", "content_additions": {"sections":[],"routes":[],"requirements":[]}, "trace": {}, "method_additions": [], "stage_work": {}, "evidence_work": {"phase":"prepare","arguments":[],"decisions":[]}}
    a = h.task_commands.submit("T1","S1",payload("A"))
    b = h.task_commands.submit("T1","S1",payload("B"))
    c = h.task_commands.submit("T1","S1",payload("A"))
    replay = h.task_commands.submit("T1","S1",payload("A"))
    assert len({a.submission_id,b.submission_id,c.submission_id}) == 3
    assert replay.submission_id == c.submission_id and not replay.created
    assert h.task_queries.section("T1","tests","report",None)["content"] == "A"


def test_section_lookup_cannot_cross_task_or_stage(project):
    h,b=bootstrap(project); add_test(b["worktree"]); fill(b); h.verify()
    row=h.task_queries.section("T1","tests","report",None)
    with pytest.raises(PoiseError): h.task_queries.section("OTHER","tests","report",row["submission"])
    with pytest.raises(PoiseError): h.task_queries.section("T1","test_review","report",row["submission"])


def test_failed_batch_is_atomic_and_foreign_keys_enabled(project):
    h,b=bootstrap(project)
    # Inject a real DB abort on event persistence: root/layers must roll back too.
    with h.store.transaction() as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        db.execute("CREATE TRIGGER fail_task_event BEFORE INSERT ON task_events BEGIN SELECT RAISE(ABORT,'test-fault'); END")
    with pytest.raises(sqlite3.IntegrityError, match="test-fault"):
        h.task_commands.submit("T1","S1",{"sections":{"report":"new"},"artifact_paths":[],"commit_message":"x", "content_additions": {"sections":[],"routes":[],"requirements":[]}, "trace": {}, "method_additions": [], "stage_work": {}, "evidence_work": {"phase":"prepare","arguments":[],"decisions":[]}})
    assert h.show()["submission_count"] == 0
    with h.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM section_layers").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        db.execute("DROP TRIGGER fail_task_event")
    assert h.task_commands.submit("T1","S1",{"sections":{"report":"new"},"artifact_paths":[],"commit_message":"x", "content_additions": {"sections":[],"routes":[],"requirements":[]}, "trace": {}, "method_additions": [], "stage_work": {}, "evidence_work": {"phase":"prepare","arguments":[],"decisions":[]}}).created


def test_old_store_is_rejected_without_migration(project):
    path=project["root"] / "state" / "state.sqlite"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE precious(value TEXT)")
        db.execute("INSERT INTO precious VALUES('do not alter')")
        db.execute("PRAGMA user_version=1")
    before=path.read_bytes()
    with pytest.raises(PoiseError, match="миграц"):
        Poise(project["config_path"],"S1")
    assert path.read_bytes() == before


def test_repositories_reject_stale_version(project):
    h,_=bootstrap(project)
    with h.store.unit_of_work() as uow:
        stale=uow.tasks.load("T1")
    h.task_commands.submit("T1","S1",{"sections":{"report":"fresh"},"artifact_paths":[],"commit_message":"x", "content_additions": {"sections":[],"routes":[],"requirements":[]}, "trace": {}, "method_additions": [], "stage_work": {}, "evidence_work": {"phase":"prepare","arguments":[],"decisions":[]}})
    with pytest.raises(PoiseError,match="верси"):
        with h.store.unit_of_work() as uow:
            uow.tasks.save(stale.cancel("S1","cancel"), stale.state.version)
    assert h.show()["status"] == "active"


def test_queries_do_not_change_revision(project):
    h,b=bootstrap(project); add_test(b["worktree"]); fill(b); h.verify()
    with h.store.unit_of_work() as uow: before=uow.tasks.load("T1").state
    h.task_queries.section("T1","tests","report",None)
    with h.store.unit_of_work() as uow: after=uow.tasks.load("T1").state
    assert before == after


def test_retired_verification_timeout_is_removed_on_next_owner_save(project):
    h, _ = bootstrap(project)
    with h.store.transaction() as db:
        row = db.execute(
            "SELECT data FROM task_methods WHERE task_id='T1' AND method_id='RED'"
        ).fetchone()
        stored = json.loads(row[0])
        stored["method"]["timeout_seconds"] = 10
        db.execute(
            "UPDATE task_methods SET data=? WHERE task_id='T1' AND method_id='RED'",
            (json.dumps(stored, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )

    assert "timeout_seconds" not in h._task()["contract"]["methods"][0]
    h.task_commands.submit(
        "T1",
        "S1",
        {
            "sections": {"report": "normalized"},
            "artifact_paths": [],
            "commit_message": "test: normalize method",
            "content_additions": {"sections": [], "routes": [], "requirements": []},
            "trace": {},
            "method_additions": [],
            "stage_work": {},
            "evidence_work": {"phase": "prepare", "arguments": [], "decisions": []},
        },
    )

    with h.store.transaction() as db:
        normalized = json.loads(db.execute(
            "SELECT data FROM task_methods WHERE task_id='T1' AND method_id='RED'"
        ).fetchone()[0])
    assert "timeout_seconds" not in normalized["method"]


def test_stored_pre_plan_method_snapshot_continues_without_revalidation(project):
    h, _ = bootstrap(project)
    with h.store.transaction() as db:
        rows = db.execute(
            "SELECT method_id,data FROM task_methods WHERE task_id='T1' ORDER BY method_id"
        ).fetchall()
        for row in rows:
            stored = json.loads(row["data"])
            stored["method"].pop("verification_plan")
            db.execute(
                "UPDATE task_methods SET data=? WHERE task_id='T1' AND method_id=?",
                (
                    json.dumps(
                        stored,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    row["method_id"],
                ),
            )

    restored = h._task()
    assert all("verification_plan" not in method for method in restored["contract"]["methods"])

    h.task_commands.submit(
        "T1",
        "S1",
        {
            "sections": {"report": "historical method remains readable"},
            "artifact_paths": [],
            "commit_message": "test: preserve historical verification method",
            "content_additions": {"sections": [], "routes": [], "requirements": []},
            "trace": {},
            "method_additions": [],
            "stage_work": {},
            "evidence_work": {"phase": "prepare", "arguments": [], "decisions": []},
        },
    )

    with h.store.transaction() as db:
        after = [json.loads(row[0]) for row in db.execute(
            "SELECT data FROM task_methods WHERE task_id='T1' ORDER BY method_id"
        )]
    assert all("verification_plan" not in item["method"] for item in after)


def test_verified_report_replay_cannot_substitute_another_tree(project):
    h,b=bootstrap(project); add_test(b["worktree"]); fill(b); report=h.verify()
    with h.store.unit_of_work() as uow:
        digest=uow.tasks.load("T1").state.submission_digest
    with pytest.raises(PoiseError,match="доклад|receipt"):
        h.task_commands.mark_verified("T1","S1",digest,{**report,"verified_tree":"unverified-tree"}, ())
    assert h.store.current("S1")["last_report"] == report


def test_execution_save_cannot_silently_ignore_lifecycle_tampering(project):
    h,_=bootstrap(project)
    data=h.store.current("S1"); data["status"]="completed"
    with pytest.raises(PoiseError,match="lifecycle|Task"):
        h.store.save(data)
    assert h.show()["status"] == "active"


def test_demo_runs_as_documented_without_missing_configuration(tmp_path):
    import os, subprocess, sys
    root=Path(__file__).resolve().parents[2]
    result=subprocess.run([sys.executable,str(root/"examples/demo.py"),"--directory",str(tmp_path/"demo")],
                          env={**os.environ,"PYTHONPATH":str(root/"src")},capture_output=True,text=True,timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "completed" in result.stdout


def test_each_verified_report_survives_rework(project):
    h,b=bootstrap(project); add_test(b['worktree']); fill(b, 'first report')
    first=h.verify()
    first_section=h.task_queries.section('T1','tests','report',None)
    b=h.bootstrap(decision='rework',feedback='Уточнить отчёт')
    fill(b,'second report'); second=h.verify()
    second_section=h.task_queries.section('T1','tests','report',None)
    assert h.task_queries.result('T1', first_section['submission']) == first
    assert h.task_queries.result('T1', second_section['submission']) == second
    assert h.show()['submission_count'] == 2


def test_section_foreign_key_rejects_unknown_owner(project):
    h,b=bootstrap(project); add_test(b['worktree']); fill(b); h.verify()
    row=h.task_queries.section('T1','tests','report',None)
    with pytest.raises(sqlite3.IntegrityError):
        with h.store.transaction() as db:
            db.execute('INSERT INTO section_layers VALUES(?,?,?,?,?)',
                       (row['submission'],'OTHER','report','foreign','populated'))


def test_cli_reads_section_without_repeating_task_id(project):
    import os, subprocess, sys
    h,b=bootstrap(project); add_test(b['worktree']); fill(b,'addressed section'); h.verify()
    result=subprocess.run([sys.executable,'-m','poise','work'],input=json.dumps({'operation':'show','input':{'queries':[{'id':'report','kind':'section','name':'report','stage':None,'submission':None,'range':None}]},'messages':[]}),
                          env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src'),
                               'POISE_CONFIG':str(project['config_path']), 'POISE_SESSION':'S1'},
                          capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'addressed section' in result.stdout
