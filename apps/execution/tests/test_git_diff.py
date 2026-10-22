"""EXE-03/05/06: exact Stage and commit comparisons through standalone CLI."""
from copy import deepcopy
import pytest

from git_diff_checks import (arrange, case, fixture, git, observed, packet, ref,
                             saved, tree_files, working)
from process_checks import invoke
from store_checks import original_rows, snapshot


CASES = [(c['id'], r['stage_id'], r) for c in fixture()['cases']
         for r in c['expected']['results']]


@pytest.mark.parametrize('name,stage,expected', CASES, ids=[f'{n}-{s}' for n,s,_ in CASES])
def test_literal_stage_diff_cases(tmp_path, monkeypatch, name, stage, expected):
    a = arrange(tmp_path, monkeypatch, name)
    before = saved(a['root'])
    canonical = original_rows(a['database'])['executions']
    other = working(a['other'])
    observed(a, packet(a, stage=stage), expected)
    assert saved(a['root']) == before
    assert original_rows(a['database'])['executions'] == canonical
    assert working(a['other']) == other
    assert not list((tmp_path/'state/git-observations').glob('*'))


def test_mode_and_utf8_names_are_changed_paths(tmp_path, monkeypatch):
    a = arrange(tmp_path, monkeypatch)
    root = a['root']
    (root/'src/result.py').write_text('A\n')
    (root/'src/result.py').chmod(0o755)
    (root/'src/данные space.py').write_text('C\n')
    code, result = invoke(a['config'], packet(a))
    assert result['status'] == 'diff_checked', result
    assert code == 0 and result['allowed'] is True
    assert result['stage_paths'] == result['commit_paths'] == [
        'src/result.py', 'src/данные space.py']
    assert tree_files(root, result['candidate_tree']) == fixture()['utf8_candidate']


@pytest.mark.parametrize('fault', ['baseline', 'repository'])
def test_diff_ordinary_git_unavailable_or_invalid_baseline_preserves_state(tmp_path, monkeypatch, fault):
    a = arrange(tmp_path, monkeypatch)
    observed(a)  # Prove this API exists before interpreting its negative result.
    before = saved(a['root'])
    db = snapshot(a['database'])
    value = packet(a, request='unavailable', revision=4)
    if fault == 'baseline':
        value['input']['baseline_ref'] = 'refs/heads/missing-stage-base'
    else:
        (a['root']/'.git').rename(a['root']/'.git-saved')
    try:
        code, result = invoke(a['config'], value)
        assert code == 2 and result['status'] == 'rejected' and result['reason']
        assert snapshot(a['database']) == db
    finally:
        if fault == 'repository':
            (a['root']/'.git-saved').rename(a['root']/'.git')
    assert saved(a['root']) == before
