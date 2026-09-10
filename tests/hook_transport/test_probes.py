import json
import sys
from copy import deepcopy
from pathlib import Path
import pytest
from harness.common import HarnessError
from harness.modules.capabilities.domain import ProbeSpec
from harness.application.capabilities import CapabilityChecks
from harness.infrastructure.capabilities import LocalProbeExecutor
from .helpers import command_probe


def run_probes(tmp_path, probes):
    return CapabilityChecks(LocalProbeExecutor(tmp_path/'receipts',0o600,{'stdout':'stdout.txt','stderr':'stderr.txt','receipt':'receipt.json'}),30).run(probes,str(tmp_path))


def test_live_commands_are_executed_once_as_batch(tmp_path):
    out=run_probes(tmp_path,[command_probe('a'),command_probe('b')])
    assert out['ready'] and len(out['observations'])==2
    for item in out['observations']:
        assert item['status']=='available' and item['observation']=='command_executed'
        assert Path(item['receipt']).is_file()


def test_failure_of_one_probe_does_not_hide_the_rest(tmp_path):
    bad=command_probe('missing', 'raise SystemExit(7)')
    out=run_probes(tmp_path,[bad,command_probe('working')])
    assert not out['ready'] and out['missing_required']==['missing']
    assert out['observations'][1]['status']=='available'


def test_invalid_batch_executes_nothing(tmp_path):
    p=command_probe();p.pop('timeout_seconds')
    with pytest.raises(HarnessError):run_probes(tmp_path,[command_probe('ok'),p])
    assert not (tmp_path/'receipts').exists()


def test_duplicate_probe_ids_rejected(tmp_path):
    with pytest.raises(HarnessError):run_probes(tmp_path,[command_probe(),command_probe()])


def test_unknown_executable_has_unavailable_receipt(tmp_path):
    p=command_probe();p['argv']=['/not/a/program']
    out=run_probes(tmp_path,[p]);assert not out['ready']
    assert out['observations'][0]['status']=='unavailable'
    assert Path(out['observations'][0]['receipt']).is_file()


def test_timeout_never_proves_a_capability(tmp_path):
    p=command_probe(code='import time; time.sleep(1)');p['timeout_seconds']=0.05
    out=run_probes(tmp_path,[p]);assert not out['ready']
    receipt=json.loads(Path(out['observations'][0]['receipt']).read_text())
    assert receipt['timed_out']


def test_json_project_binding_is_verified_not_inferred(tmp_path):
    p=command_probe(code='import os,json;print(json.dumps({"project":os.getcwd()}))')
    p['stdout_contains']=[];p['project_bound']=True
    p['json_assertions']=[{'path':['project'],'equals':'${workspace}'}]
    assert run_probes(tmp_path,[p])['ready']
    p['argv'][-1]='import json;print(json.dumps({"project":"/wrong"}))'
    assert not run_probes(tmp_path,[p])['ready']


def test_project_claim_requires_explicit_workspace_predicate():
    p=command_probe();p['project_bound']=True
    with pytest.raises(HarnessError):ProbeSpec.parse(p)


def test_output_limit_and_optional_unavailable(tmp_path):
    p=command_probe(code='print("ready"*20000)',required=False);p['max_output_bytes']=100
    out=run_probes(tmp_path,[p]);assert out['ready']
    assert out['observations'][0]['status']=='unavailable'


def test_identity_probe_records_null_version_unless_observed(tmp_path):
    out=run_probes(tmp_path,[command_probe()])
    assert out['observations'][0]['version'] is None


def test_explicit_environment_no_inherited_secrets(tmp_path,monkeypatch):
    monkeypatch.setenv('HARNESS_PRIVATE_SECRET','secret')
    p=command_probe(code='import os;print("ready" if "HARNESS_PRIVATE_SECRET" not in os.environ else "leaked")')
    assert run_probes(tmp_path,[p])['ready']
