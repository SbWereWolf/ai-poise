"""Literal shell requests at the shipped tool event boundary, not a copied parser."""
from copy import deepcopy
import shlex
import subprocess

import pytest

from tests.command_intake.helpers import cases, each_port, fixture


@pytest.mark.parametrize('case', cases('classifier'), ids=lambda x:x['id'])
def test_classifier_contract(case, project, tmp_path, monkeypatch):
    for i in each_port(project, tmp_path, monkeypatch):
        number = case['id'][:3]
        if number == 'C17':
            explicit_checkout(i, tmp_path / i.port)
        elif number == 'C18':
            unknown_checkout(i)
        else:
            for row in fixture('classifier.json')[number]:
                i.policy = fixture('policy.json')
                # Registry repeat policy must not disguise a classifier refusal.
                i.configure(**{'registry.mode':'audit', **row['policy']})
                i.s.write('docs/имя с пробелом.txt', b'quoted file body\n')
                i.s.write('docs/program.sed', b'1,2p\n')
                modes = ['audit','enforce'] if number == 'C16' else ['enforce']
                for mode in modes:
                    i.configure(**{'classifier.mode':mode})
                    prior = i.coverage()['details']['counts']['pending']
                    call, native, pre = i.pre(row['command'])
                    assert not (i.s.checkout/'classification-executed').exists()
                    forbidden = row['reason'] is not None
                    if forbidden:
                        assert pre['decision']['reason_code'] == row['reason'], pre
                        assert pre['decision']['action'] == ('audit' if mode=='audit' else 'deny')
                        assert pre['decision']['message'] and pre['decision']['action_hint']
                        if mode == 'enforce':
                            assert native['hookSpecificOutput']['permissionDecision'] == 'deny'
                            assert i.coverage()['details']['counts']['pending'] == prior
                            assert i.plans(call) == []
                        else:
                            assert native.get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
                    else:
                        assert pre['decision']['action'] in {'allow','audit','none'}, pre
                        if (row['run'] or 'simulated_response_from' in row) and row['kind'] not in {'none', None}:
                            if 'simulated_response_from' in row:
                                # Parser/accounting input only: this does not
                                # claim an installed bat/batcat was executed.
                                body = fixture('cases.json')['common_vectors']['files'][row['simulated_response_from']]
                                response = {'output':body,'exit_code':0}
                            else:
                                response = i.actual(call)
                            assert response['exit_code'] == 0
                            _, post = i.post(call, response)
                            assert post['status'] == 'settled', post
                            plans = i.plans(call)
                            assert plans
                            for _, _, _, plan in plans:
                                assert plan['coverage_kind'] == row['kind']
                                if row['ranges'] is not None:
                                    assert plan['line_ranges'] == row['ranges']
                        elif row['kind'] == 'none':
                            assert pre['status'] == 'ignored' and i.plans(call) == []
                        elif row['reason'] is None:
                            # Classification-only unknown shape is not executed.
                            assert i.plans(call) == []


def explicit_checkout(i, root):
    original = i.s.checkout
    original_key = i.coverage()['context']['checkout_key']
    target = root / 'checkout B with spaces'
    cp = subprocess.run(['git','-C',str(original),'worktree','add','-b','command-copy',str(target)],
                        capture_output=True, check=False)
    assert cp.returncode == 0, cp.stderr
    hidden = root / 'hidden-C'; hidden.mkdir()
    i.s.checkout = target
    i.s.write('relative.txt', fixture('cases.json')['common_vectors']['files']['A'].encode())
    target_key = i.coverage()['context']['checkout_key']
    assert target_key != original_key
    i.s.checkout = original
    i.s.write('relative.txt', b'wrong A body\n')
    (hidden / 'relative.txt').write_text('wrong C body\n')
    for reader in ["sed -n '1,4p' relative.txt", 'cat relative.txt | head -n4']:
        call, _, pre = i.pre(i.command(reader, target), raw=True, cwd=str(original))
        assert pre['context']['checkout_key'] == target_key, pre
        cp = subprocess.run(['bash','-c',i.calls[call]['tool_input']['command']],
                            capture_output=True,text=True,cwd=hidden,check=False)
        assert cp.returncode == 0 and cp.stdout == '01 A\n02 A\n03 A\n04 A\n'
        _, post = i.post(call, {'stdout':cp.stdout,'stderr':cp.stderr,'exit_code':cp.returncode})
        assert post['context']['checkout_key'] == target_key
        i.s.checkout = target
        document = i.coverage()['details']['documents']
        assert any(d['path']=='relative.txt' and d['ranges']==[[1,4]] for d in document)
        i.s.checkout = original
        assert i.coverage()['details']['counts']['current_documents'] == 0
        i.configure(**{'registry.mode':'audit'})


def unknown_checkout(i):
    for mode in ['audit','enforce']:
        i.mode(mode)
        for reader in ["sed -n '1,4p' docs/A.txt", 'cd "$DYNAMIC" && cat docs/A.txt',
                       'cd /missing || cat docs/A.txt', 'cd /missing; cat docs/A.txt']:
            call, native, pre = i.pre(reader, raw=True)
            assert pre['decision']['reason_code'] == 'unresolved_execution_cwd', pre
            assert pre['decision']['action'] == ('audit' if mode=='audit' else 'deny')
            assert i.plans(call) == []
            if mode == 'enforce':
                assert native['hookSpecificOutput']['permissionDecision'] == 'deny'
        event = i.event_value('PreToolUse', 'cat docs/A.txt', 'invented-cwd')
        event['tool_input']['workdir'] = str(i.s.checkout)
        _, outcome = i.hook(event)
        assert outcome['status'] in {'denied','ignored'} or outcome['decision']['reason_code']=='unresolved_execution_cwd'
        i.counts(pending=0, current=0)
    i.mode('enforce')
    i.read('head -n2 ' + shlex.quote(str(i.s.checkout/'docs/A.txt')))
    i.ranges([[1,2]])
