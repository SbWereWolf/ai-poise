import copy
import json
import sqlite3
from pathlib import Path
import pytest
from poise.common import digest
from poise.modules.foundation.errors import PoiseError
from poise.composition import goal_config_tools
from tests.goal_config.helpers import template, additions, request, settings


def test_create_change_repeat_and_old_repeat_do_not_rollback(tmp_path):
    path, selection=settings(tmp_path)
    api=goal_config_tools(path)
    new=request("create","writing",None,[],selection=selection)
    first=api.apply_batch(new)
    assert first["status"]=="applied" and first["changed"]
    again=goal_config_tools(path).apply_batch(new)
    assert again["replayed"] and again["revision"]==first["revision"]
    update=request("update","writing",first["revision"],additions(),"req-2")
    second=api.apply_batch(update)
    assert second["revision"]!=first["revision"]
    assert api.apply_batch(new)["current_revision"]==second["revision"]
    assert json.loads((tmp_path/"config/processes/writing.json").read_text())["content_contract"]["sections"]


def test_invalid_batch_does_not_publish_half_the_changes(tmp_path):
    path, selection=settings(tmp_path); api=goal_config_tools(path)
    one=api.apply_batch(request("create","writing",None,[],selection=selection))
    target=Path(one["config_path"]); before=target.read_bytes()
    with pytest.raises(PoiseError):
        api.apply_batch(request("update","writing",one["revision"],additions()+[{"op":"patch_stage","id":"draft","set":{"transitions":{"complete":"MISSING"}}}],"bad"))
    assert target.read_bytes()==before


def test_stale_revision_and_reused_request_are_rejected(tmp_path):
    path, selection=settings(tmp_path); api=goal_config_tools(path)
    one=api.apply_batch(request("create","writing",None,[],selection=selection))
    with pytest.raises(PoiseError):
        api.apply_batch(request("update","writing","0"*64,additions(),"update"))
    with pytest.raises(PoiseError):
        api.apply_batch(request("update","writing",one["revision"],additions(),"req-1"))


def test_existing_valid_config_can_be_edited_and_other_pack_is_unchanged(tmp_path):
    path, selection=settings(tmp_path)
    target=tmp_path/"config/processes/writing.json"; target.parent.mkdir(parents=True)
    target.write_text(json.dumps(template()))
    other=tmp_path/"config/processes/other.json"; other.write_text("do not touch")
    result=goal_config_tools(path).apply_batch(request("update","writing",digest(template()),additions()))
    assert result["changed"] and other.read_text()=="do not touch"


def test_noop_is_not_an_extra_content_revision(tmp_path):
    path, selection=settings(tmp_path); api=goal_config_tools(path)
    first=api.apply_batch(request("create","writing",None,[],selection=selection))
    second=api.apply_batch(request("update","writing",first["revision"],[],"noop"))
    assert second["changed"] is False and second["revision"]==first["revision"]


@pytest.mark.parametrize("edit",[
 lambda r:r.update(template=None),
 lambda r:r["template"].update(version="unknown"),
 lambda r:r["template"].update(digest="0"*64),
 lambda r:r.update(goal_type="../escape"),
 lambda r:r.update(expected_revision="0"*64),
 lambda r:r.pop("changes"),
])
def test_explicit_inputs_no_defaults_no_template_guessing(tmp_path, edit):
    path, selection=settings(tmp_path)
    raw=request("create","writing",None,[],selection=selection); edit(raw)
    with pytest.raises(PoiseError): goal_config_tools(path).apply_batch(raw)
    assert not (tmp_path/"config/processes/writing.json").exists()


def test_missing_setting_and_oversized_batch_are_errors(tmp_path):
    path, selection=settings(tmp_path)
    cfg=json.loads(path.read_text()); del cfg["max_changes"]; path.write_text(json.dumps(cfg))
    with pytest.raises(PoiseError): goal_config_tools(path)
    path, selection=settings(tmp_path); cfg=json.loads(path.read_text()); cfg["max_changes"]=1; path.write_text(json.dumps(cfg))
    with pytest.raises(PoiseError): goal_config_tools(path).apply_batch(request("create","writing",None,additions(),selection=selection))


def test_full_template_digest_is_checked_at_execution(tmp_path):
    path, selection=settings(tmp_path)
    (tmp_path/"templates/writing.json").write_text("{}")
    with pytest.raises(PoiseError): goal_config_tools(path).apply_batch(request("create","writing",None,[],selection=selection))


def test_symlink_cannot_write_outside_config_root(tmp_path):
    path, selection=settings(tmp_path)
    external=tmp_path.parent/(tmp_path.name+"-foreign.json"); external.write_text("untouched")
    target=tmp_path/"config/processes/writing.json"; target.parent.mkdir(parents=True); target.symlink_to(external)
    with pytest.raises(PoiseError): goal_config_tools(path).apply_batch(request("create","writing",None,[],selection=selection))
    assert external.read_text()=="untouched"


def test_atomic_file_failure_can_resume_without_duplicate_revision(tmp_path, monkeypatch):
    import poise.infrastructure.goal_config as infra
    path, selection=settings(tmp_path); api=goal_config_tools(path)
    original=infra.atomic_write
    def fail(path, content, mode):
        if path.name=="writing.json": raise OSError("simulated full disk before replacement")
        return original(path,content,mode)
    raw=request("create","writing",None,[],selection=selection)
    with monkeypatch.context() as m:
        m.setattr(infra,"atomic_write",fail)
        with pytest.raises(PoiseError,match="pending|публикац"):
            api.apply_batch(raw)
    assert not (tmp_path/"config/processes/writing.json").exists()
    out=goal_config_tools(path).apply_batch(raw)
    assert out["replayed"] and out["revision"]==digest(template())
    with sqlite3.connect(tmp_path/"state/config-editor.sqlite") as db:
        assert db.execute("select count(*) from config_operations").fetchone()[0]==1


def test_many_related_changes_are_one_operation(tmp_path):
    path, selection=settings(tmp_path)
    changes=[]
    for n in range(120):
        changes += [{"op":"put_section","value":{"id":f"s{n}","template":"fill","normalization":"strip","write_stages":["draft"]}},
        {"op":"put_requirement","value":{"id":f"r{n}","kind":"section","stages":["draft"],"phase":"pre","section":f"s{n}","states":["populated"]}}]
    out=goal_config_tools(path).apply_batch(request("create","writing",None,changes,selection=selection))
    assert out["change_count"]==240
    assert len(json.loads(Path(out["config_path"]).read_text())["content_contract"]["requirements"])==120


def test_lost_receipt_after_atomic_replace_recovers_known_candidate(tmp_path, monkeypatch):
    import poise.infrastructure.goal_config as infra
    path, selection=settings(tmp_path); raw=request('create','writing',None,[],selection=selection)
    original=infra.atomic_write
    def replace_then_interrupt(path,content,mode):
        original(path,content,mode)
        raise OSError('crash simulation after file replacement')
    with monkeypatch.context() as m:
        m.setattr(infra,'atomic_write',replace_then_interrupt)
        with pytest.raises(PoiseError): goal_config_tools(path).apply_batch(raw)
    target=tmp_path/'config/processes/writing.json'
    before=target.read_bytes()
    result=goal_config_tools(path).apply_batch(raw)
    assert result['replayed'] and target.read_bytes()==before
    with sqlite3.connect(tmp_path/'state/config-editor.sqlite') as db:
        assert db.execute("select count(*) from config_operations where phase='completed'").fetchone()[0]==1


def test_direct_external_file_change_is_not_silently_adopted(tmp_path):
    path, selection=settings(tmp_path); api=goal_config_tools(path)
    first=api.apply_batch(request('create','writing',None,[],selection=selection))
    file=Path(first['config_path']); data=json.loads(file.read_text()); data['stages'][0]['instruction']='external change'
    file.write_text(json.dumps(data))
    with pytest.raises(PoiseError,match='вне редактора'):
        api.apply_batch(request('update','writing',digest(data),[], 'external'))
    assert json.loads(file.read_text())==data


def test_incompatible_editor_store_is_not_migrated(tmp_path):
    path,selection=settings(tmp_path)
    dbpath=tmp_path/'state/config-editor.sqlite'; dbpath.parent.mkdir()
    with sqlite3.connect(dbpath) as db: db.execute('PRAGMA user_version=999')
    before=dbpath.read_bytes()
    with pytest.raises(PoiseError,match='миграци'):
        goal_config_tools(path).apply_batch(request('create','writing',None,[],selection=selection))
    assert dbpath.read_bytes()==before


def test_pending_publication_cannot_be_redirected_by_new_settings(tmp_path, monkeypatch):
    import poise.infrastructure.goal_config as infra
    path,selection=settings(tmp_path); raw=request('create','writing',None,[],selection=selection)
    with monkeypatch.context() as m:
        def fail(*args): raise OSError('interrupted write')
        m.setattr(infra,'atomic_write',fail)
        with pytest.raises(PoiseError): goal_config_tools(path).apply_batch(raw)
    cfg=json.loads(path.read_text()); cfg['processes']['writing']='config/processes/elsewhere.json';path.write_text(json.dumps(cfg))
    with pytest.raises(PoiseError): goal_config_tools(path).apply_batch(raw)
    assert not (tmp_path/'config/processes/elsewhere.json').exists()


@pytest.mark.parametrize('reverse',[False,True])
def test_section_gate_stage_and_transition_published_as_one_candidate(tmp_path,reverse):
    path,selection=settings(tmp_path)
    node=copy.deepcopy(template()['stages'][0]); node.update(id='prepare',transitions={'complete':'draft'},rework_targets=['prepare'])
    changes=[
      {'op':'put_requirement','value':{'id':'pre-required','kind':'section','stages':['prepare'],'phase':'pre','section':'basis','states':['populated']}},
      {'op':'patch_route','set':{'entry':'prepare'}},
      {'op':'put_section','value':{'id':'basis','template':'Fill basis.','normalization':'strip','write_stages':['prepare']}},
      {'op':'put_stage','value':node}]
    if reverse: changes.reverse()
    result=goal_config_tools(path).apply_batch(request('create','writing',None,changes,selection=selection))
    cfg=json.loads(Path(result['config_path']).read_text())
    assert cfg['route']['entry']=='prepare'
    assert cfg['content_contract']['requirements'][0]['stages']==['prepare']
    assert next(s for s in cfg['stages'] if s['id']=='prepare')['transitions']=={'complete':'draft'}
