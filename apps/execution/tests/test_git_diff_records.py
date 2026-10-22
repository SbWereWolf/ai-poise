"""Own observation records/replay and short transaction boundaries."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from git_diff_checks import (APP, arrange, fixture, git_wrapper, observed, packet,
                             put_working, query_packet, saved, working, tree, ref)
from process_checks import PROVIDER, invoke, write_json
from store_checks import original_rows, snapshot, stored_records


def test_diff_result_is_own_exact_record_and_query(tmp_path, monkeypatch):
    a = arrange(tmp_path, monkeypatch)
    before = original_rows(a['database'])
    result = observed(a)
    rows = [r for r in stored_records(a['database']) if r['kind'] == 'file_diff_decision']
    assert len(rows) == 1
    literal = a['case']['expected']
    data = {key:literal['results'][0][key] for key in ['stage_id','role','allowed','decisions']}
    data.update(context_id='scope-1', root=a['case']['root'], baseline_tree=a['entry'],
        head_commit=a['head'], head_tree=ref(a['root'],'HEAD^{tree}'),
        candidate_tree=a['candidate'], stage_paths=literal['stage_paths'],
        commit_paths=literal['commit_paths'])
    data.update(request_id='diff-1', baseline_ref=a['entry'],
        contract_revision=a['contract_revision'],
        process_ref={'goal_type':'writing','revision':a['process_revision']},workspace_id='workspace-0042')
    expected = {'task_id':'0042','kind':'file_diff_decision','id':result['result_id'],
                'revision':1,'generation':1,'data':data}
    assert rows == [expected]
    code, queried = invoke(a['config'], query_packet())
    assert code == 0
    assert queried == {'schema':'stride-execution/v1','status':'read_only','revision':4,
        'replayed':False,'results':[{'id':'own-diff','kind':'file_diff_result','result':expected}]}
    code, absent = invoke(a['config'], query_packet('not-created'))
    assert code == 0 and absent['results'] == [
        {'id':'own-diff','kind':'file_diff_result','result':None}]
    assert original_rows(a['database'])['executions'] == before['executions']


def test_diff_exact_replay_does_not_reobserve_changed_git(tmp_path, monkeypatch):
    a = arrange(tmp_path, monkeypatch)
    value = packet(a)
    result = observed(a, value)
    (a['root']/'src/result.py').write_text('C\n')
    (a['root']/'.git').rename(a['root']/'.git-saved')
    before = snapshot(a['database'])
    try:
        code, replay = invoke(a['config'], value)
        assert code == 0 and replay == dict(result, replayed=True)
        changed = deepcopy(value)
        changed['input']['stage_id'] = 'integration'
        code, conflict = invoke(a['config'], changed)
        assert code == 2 and conflict['status'] == 'rejected' and conflict['reason']
        assert snapshot(a['database']) == before
    finally:
        (a['root']/'.git-saved').rename(a['root']/'.git')
    assert (a['root']/'src/result.py').read_text() == 'C\n'


def test_corrected_owned_work_uses_fresh_request_and_preserves_other_context(tmp_path, monkeypatch):
    a = arrange(tmp_path, monkeypatch, 'preentry-extra')
    denied = observed(a)
    assert denied['allowed'] is False
    # Owner transfers its foreign change to its context before new observation.
    foreign = a['other']/'extra/foreign.py'
    foreign.parent.mkdir()
    foreign.write_text('B\n')
    (a['root']/'extra/foreign.py').write_text('A\n')
    expected_other = working(a['other'])
    before_index = (a['root']/'.git/index').read_bytes()
    value = packet(a, request='corrected', revision=4)
    value['input']['baseline_ref'] = tree(a['root'], fixture()['corrected_baseline'])
    code, result = invoke(a['config'], value)
    assert code == 0 and result['status'] == 'diff_checked' and result['allowed'] is True
    assert result['commit_paths'] == ['src/result.py']
    assert result['stage_paths'] == []
    assert working(a['other']) == expected_other
    assert (a['root']/'.git/index').read_bytes() == before_index


def test_diff_revision_change_during_git_leaves_no_half_result(tmp_path, monkeypatch):
    a = arrange(tmp_path, monkeypatch)
    observed(a)
    wrapper = git_wrapper(a, monkeypatch, 'revision')
    bump = fixture()['revision_bump']
    monkeypatch.setenv('T052_PROVIDER',str(PROVIDER))
    monkeypatch.setenv('T052_CONFIG',str(a['config']))
    monkeypatch.setenv('T052_BUMP',json.dumps(bump))
    before = saved(a['root'])
    code, result = invoke(a['config'], packet(a, request='race', revision=4))
    assert code == 2 and result['status'] == 'rejected' and result['reason']
    assert json.loads((wrapper/'observed.response').read_text())['status'] == 'checked'
    assert original_rows(a['database'])['revision'] == 5
    assert len([r for r in stored_records(a['database']) if r['kind']=='file_diff_decision']) == 1
    assert 'race' not in [r[0] for r in original_rows(a['database'])['requests']]
    assert saved(a['root']) == before


@pytest.mark.parametrize('field,value', [('context_id','missing'),('stage_id','missing'),
    ('root',{'kind':'code','position':8}),('generation',2)])
def test_diff_unknown_context_stage_root_or_generation_is_effect_free(tmp_path, monkeypatch, field, value):
    a = arrange(tmp_path, monkeypatch)
    observed(a)
    before, db = saved(a['root']), snapshot(a['database'])
    request = packet(a, request='invalid', revision=4)
    request['input'][field] = value
    code, result = invoke(a['config'], request)
    assert code == 2 and result['status'] == 'rejected' and result['reason']
    assert saved(a['root']) == before and snapshot(a['database']) == db


@pytest.mark.parametrize('change', ['absent','seconds','directory'])
def test_git_settings_are_explicit_and_other_existing_operations_still_work(tmp_path, monkeypatch, change):
    a = arrange(tmp_path, monkeypatch)
    observed(a)
    data = json.loads(a['config'].read_text())
    if change == 'absent':
        del data['git']
    elif change == 'seconds':
        data['git']['seconds'] = 0
    else:
        data['git']['observations'] = '../outside-state'
    write_json(a['config'],data)
    before, db = saved(a['root']), snapshot(a['database'])
    code, result = invoke(a['config'], packet(a, request='config', revision=4))
    assert code == 2 and result['status'] == 'rejected' and result['reason']
    assert saved(a['root']) == before and snapshot(a['database']) == db
    if change == 'absent':
        code, result = invoke(a['config'], fixture()['query_execution'])
        assert code == 0 and result['results'][0]['value']['task_id'] == '0042'
