"""Contract tests for orchestration verdicts, not the application acceptance cases."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('pmi_runner',ROOT/'acceptance/run_pmi.py')
pmi=importlib.util.module_from_spec(spec)
spec.loader.exec_module(pmi)


def fake(tmp_path,status='PASS',code=0,body=None):
    script=tmp_path/'case.py'
    script.write_text(body or ('import pathlib,json\n'+f'pathlib.Path("result.json").write_text(json.dumps({{"status":{status!r}}}))\nraise SystemExit({code})\n'))
    return pmi.execute_step([sys.executable,str(script)],tmp_path,1,{},'fake')


def test_exit_zero_cannot_hide_failed_assertion(tmp_path):
    assert fake(tmp_path,status='FAIL')['status']=='FAIL'


def test_report_pass_cannot_hide_nonzero_exit(tmp_path):
    assert fake(tmp_path,code=1)['status']=='FAIL'


def test_missing_result_is_not_success(tmp_path):
    assert fake(tmp_path,body='print("success without evidence")')['status']=='FAIL'


def test_timeout_is_not_success(tmp_path):
    out=fake(tmp_path,body='import time;time.sleep(5)')
    assert out['status']=='FAIL' and out['timed_out']


def test_success_requires_matching_verdict_and_exit(tmp_path):
    assert fake(tmp_path)['status']=='PASS'


def test_protocol_record_has_output_hashes(tmp_path):
    result=fake(tmp_path)
    assert result['stdout_sha256']==pmi.sha256(tmp_path/'pmi-stdout.log')
    assert result['stderr_sha256']==pmi.sha256(tmp_path/'pmi-stderr.log')


def test_blocked_case_is_not_pass(tmp_path):
    assert fake(tmp_path,status='BLOCKED',code=3)['status']=='BLOCKED'


@pytest.mark.parametrize('ids',[['same','same'],['../escape'],[]])
def test_bad_step_identity_rejected(ids):
    manifest={'schema':'poise/pmi/v1','id':'v1','profiles':{'portable':{}},'steps':[{'id':x,'case':'taskless','timeout_seconds':30} for x in ids]}
    with pytest.raises(ValueError):pmi.validate(manifest)


def test_profile_mismatch_is_reported_not_ignored():
    errors=pmi.environment_errors({'os_ids':['ubuntu'],'versions':['24.04'],'python':[3,13]},
                                  {'os_id':'debian','os_version':'13','python':[3,13],'uid':1000})
    assert errors


def test_resume_requires_exact_frozen_inputs():
    with pytest.raises(ValueError):pmi.validate_resume({'fingerprint':'one'},{'fingerprint':'two'})
