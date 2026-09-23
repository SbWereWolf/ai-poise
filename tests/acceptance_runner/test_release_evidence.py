"""Release attribution must survive source drift and must not trust a verdict alone."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'acceptance'))
import release_evidence as proof


def test_source_fingerprint_changes_when_bytes_change(tmp_path):
    p = tmp_path / 'a.py'; p.write_text('first')
    before = proof.source_tree(tmp_path)
    p.write_text('other')
    assert proof.source_tree(tmp_path) != before


def test_source_fingerprint_includes_permissions(tmp_path):
    p = tmp_path / 'launch'; p.write_text('hi'); p.chmod(0o644)
    before = proof.source_tree(tmp_path); p.chmod(0o755)
    assert proof.source_tree(tmp_path) != before


def test_cache_is_not_a_source_change(tmp_path):
    p = tmp_path / 'code.py'; p.write_text('pass')
    before = proof.source_tree(tmp_path)
    (tmp_path / '__pycache__').mkdir(); (tmp_path / '__pycache__/code.pyc').write_bytes(b'cache')
    assert proof.source_tree(tmp_path) == before


@pytest.mark.parametrize('status', ['RUNNING', 'FAIL', 'BLOCKED'])
def test_non_pass_cannot_authorize_installation(status):
    with pytest.raises(ValueError):
        proof.validate_acceptance({'status': status}, {'steps': []})


def test_missing_case_cannot_authorize_installation():
    with pytest.raises(ValueError):
        proof.validate_acceptance({'status':'PASS','steps':[]}, {'steps':[{'id':'PSI-01'}]})


def test_changed_result_evidence_is_rejected(tmp_path):
    f=tmp_path/'result.json'; f.write_text('{"status":"PASS"}')
    h=proof.sha256(f)
    report={'status':'PASS','steps':[{'id':'PSI-01','status':'PASS','exit_code':0,
        'result':str(f),'result_sha256':h,'stdout':str(f),'stdout_sha256':h,'stderr':str(f),'stderr_sha256':h}]}
    proof.validate_acceptance(report, {'steps':[{'id':'PSI-01'}]})
    f.write_text('{"status":"FAIL"}')
    with pytest.raises(ValueError): proof.validate_acceptance(report, {'steps':[{'id':'PSI-01'}]})


def test_duplicate_case_cannot_replace_missing_case():
    with pytest.raises(ValueError):
        proof.validate_acceptance({'status':'PASS','steps':[{'id':'A'},{'id':'A'}]}, {'steps':[{'id':'A'},{'id':'B'}]})


def test_installer_rejects_existing_destination_without_changes(tmp_path):
    import subprocess
    existing = tmp_path / 'existing'; existing.mkdir()
    marker = existing / 'important.txt'; marker.write_text('preserve')
    result = subprocess.run([sys.executable, str(ROOT/'acceptance/create_installation.py'),
        '--protocol',str(tmp_path/'absent.json'),'--manifest',str(tmp_path/'absent-pmi.json'),
        '--python',sys.executable,'--source',str(ROOT),'--repository',str(tmp_path),
        '--destination',str(existing),'--project','p','--base-ref','main',
        '--author-name','test','--author-email','test@example.invalid'],capture_output=True,text=True)
    assert result.returncode == 2 and 'must be new' in result.stdout
    assert marker.read_text() == 'preserve'


def test_installer_rejects_incomplete_acceptance_before_creation(tmp_path):
    import subprocess
    report = tmp_path / 'report.json'; report.write_text('{"status":"RUNNING"}')
    manifest = tmp_path / 'pmi.json'; manifest.write_text('{"steps":[{"id":"PSI-01"}]}')
    dest = tmp_path/'new'
    result = subprocess.run([sys.executable,str(ROOT/'acceptance/create_installation.py'),
        '--protocol',str(report),'--manifest',str(manifest),'--python',sys.executable,
        '--source',str(ROOT),'--repository',str(tmp_path),'--destination',str(dest),
        '--project','p','--base-ref','main','--author-name','test','--author-email','test@example.invalid'],
        capture_output=True,text=True)
    assert result.returncode == 2
    assert not dest.exists()
