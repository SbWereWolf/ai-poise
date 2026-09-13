from pathlib import Path
from copy import deepcopy
import json
import sqlite3
import zipfile
import pytest
from poise.common import PoiseError,file_digest
from conftest import WorkPoise as Poise
from poise.application.work import WorkTools
from .helpers import destination,restore,export,handoff_args
from .test_paths import prepared


def test_changed_archive_digest_rejected_before_destination_side_effects(project,tmp_path):
    h,a,c,payload=prepared(project);saved=export(a,handoff=handoff_args(payload))
    path=Path(saved['package_path']);path.write_bytes(path.read_bytes()+b'bad')
    dst=destination(project,tmp_path/'dst');b=WorkTools(Poise(dst['config_path'],'receiver'))
    with pytest.raises(PoiseError,match='digest|integrity'):
        restore(b,path,saved['package_digest'])
    assert b.runtime.task_queries.record('T1') is None
    assert not (b.runtime.state/b.runtime.paths['worktrees']/'T1').exists()


def test_zip_traversal_rejected_before_any_task_is_visible(project,tmp_path):
    h,a,c,payload=prepared(project);saved=export(a,handoff=handoff_args(payload))
    path=Path(saved['package_path'])
    with zipfile.ZipFile(path,'a') as z:z.writestr('../escape','bad')
    dst=destination(project,tmp_path/'dst');b=WorkTools(Poise(dst['config_path'],'receiver'))
    with pytest.raises(PoiseError):restore(b,path,file_digest(path))
    assert b.runtime.task_queries.record('T1') is None
    assert not (dst['root']/'escape').exists()


def test_missing_packaged_file_rejected(project,tmp_path):
    h,a,c,payload=prepared(project);saved=export(a,handoff=handoff_args(payload))
    original=Path(saved['package_path']);bad=tmp_path/'missing.zip'
    with zipfile.ZipFile(original) as src,zipfile.ZipFile(bad,'w') as dst:
        removed=False
        for item in src.infolist():
            if item.filename.startswith('bundles/') and not removed:removed=True;continue
            dst.writestr(item,src.read(item))
    target=destination(project,tmp_path/'dst');b=WorkTools(Poise(target['config_path'],'receiver'))
    with pytest.raises(PoiseError):restore(b,bad,file_digest(bad))
    assert b.runtime.task_queries.record('T1') is None


def test_import_transaction_failure_leaves_no_partial_task_and_can_retry(project,tmp_path):
    h,a,c,payload=prepared(project);saved=export(a,handoff=handoff_args(payload))
    dst=destination(project,tmp_path/'dst');b=WorkTools(Poise(dst['config_path'],'receiver'))
    with b.runtime.store.transaction() as db:
        db.execute("CREATE TRIGGER block_import BEFORE INSERT ON section_layers BEGIN SELECT RAISE(ABORT,'fixture refusal'); END")
    with pytest.raises((PoiseError,sqlite3.DatabaseError)):
        restore(b,saved['package_path'],saved['package_digest'])
    with b.runtime.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM tasks WHERE id='T1'").fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM submissions').fetchone()[0]==0
        db.execute('DROP TRIGGER block_import')
    assert restore(b,saved['package_path'],saved['package_digest'])['status']=='imported'


def test_task_file_symlink_is_not_followed_during_export(project,tmp_path):
    h,a,c,payload=prepared(project);export(a,handoff=handoff_args(payload))
    root=h.state/h.paths['standalone_tasks']/'T1';outside=tmp_path/'secret';outside.write_text('not task data')
    (root/'outside').symlink_to(outside)
    with pytest.raises(PoiseError,match='symlink|regular'):
        export(a,ids=['T1'],request_id='symlink')


def test_configuration_has_no_implicit_transfer_limits(project):
    from .helpers import enabled
    from conftest import write_json
    enabled(project);del project['cfg']['runtime_services']['transfer']['max_total_bytes']
    write_json(project['config_path'],project['cfg'])
    with pytest.raises(PoiseError):Poise(project['config_path'],'source')


def test_export_checks_registered_artifact_digest_not_only_new_copy(project):
    from batch.helpers import request
    h,a,c,payload=prepared(project)
    out=a.invoke(request('artifacts',{'items':[{'scope':'task','path':'stable.txt','source':{'kind':'text','text':'original'}}]}))
    payload['artifact_paths']=out['artifact_paths']
    export(a,handoff=handoff_args(payload))
    Path(out['artifact_paths'][0]).write_text('changed after registration')
    with pytest.raises(PoiseError,match='artifact|Artifact'):
        export(a,ids=['T1'],request_id='changed-artifact')


def test_export_import_two_tasks_is_one_batch_and_preserves_distinct_submissions(project,tmp_path):
    from batch.helpers import bootstrap,result,verify
    from conftest import add_test
    from .helpers import pick
    h,a,c,payload=prepared(project);export(a,handoff=handoff_args(payload))
    t=deepcopy(project['task']);t['id']='T2'
    c2=bootstrap(a,dict(project,task=t));add_test(c2['worktree'])
    checkpoint=handoff_args(result(c2,'Second task draft'));checkpoint['request_id']='second'
    saved=export(a,ids=['T1','T2'],request_id='batch',handoff=checkpoint)
    dst=destination(project,tmp_path/'dst');b=WorkTools(Poise(dst['config_path'],'receiver'))
    receipt=restore(b,saved['package_path'],saved['package_digest'])
    assert receipt['task_ids']==['T1','T2']
    current=pick(b,'T2');assert current['result_template']['sections']['report']=='Second task draft'
    assert verify(b,current['result_template'])['status']=='verified'
    assert b.runtime.task_queries.record('T1')['claimed_by'] is None


def test_artifact_id_does_not_depend_on_physical_root(tmp_path):
    from poise.artifacts import inspect_paths
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    (a/'note.txt').write_text('same');(b/'note.txt').write_text('same')
    one=inspect_paths([str(a/'note.txt')],{'task':a},{'task':'T'})
    two=inspect_paths([str(b/'note.txt')],{'task':b},{'task':'T'})
    assert one[0]['id']==two[0]['id']


def test_snapshot_fingerprint_ignores_query_row_order_but_not_content():
    from poise.infrastructure.sqlite.transfers import snapshot_fingerprint
    first={'tasks':[{'id':'A'},{'id':'B'}],'journal':[]}
    second={'tasks':[{'id':'B'},{'id':'A'}],'journal':[{'seq':9}]}
    assert snapshot_fingerprint(first)==snapshot_fingerprint(second)
    second['tasks'][0]['id']='C'
    assert snapshot_fingerprint(first)!=snapshot_fingerprint(second)
