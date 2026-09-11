"""Managed settings are created by one tool, never by the model editing files."""
import json
from pathlib import Path
import subprocess
import sys
from copy import deepcopy
import pytest
from poise.common import PoiseError
from hook_transport.helpers import settings, definition


def prepared(project,tmp_path):
    # A fixture supplies a complete explicit configuration; the tool must write it.
    path=settings(project,tmp_path);raw=json.loads(path.read_text());path.unlink()
    return path,{'settings':raw,'installation':{'request_id':'setup','expected_revision':None,'definition':definition()}}


def test_setup_one_packet_creates_settings_hooks_and_runs_probes(project,tmp_path):
    from poise.infrastructure.hook_transport import setup_runtime
    path,packet=prepared(project,tmp_path)
    result=setup_runtime(path,packet)
    assert result['status']=='installed' and result['capability_checks']['ready']
    assert json.loads(path.read_text())==packet['settings']
    assert Path(result['hooks_path']).is_file()
    repeat=setup_runtime(path,packet)
    assert repeat['revision']==result['revision']
    assert len(json.loads(Path(result['hooks_path']).read_text())['hooks']['SessionStart'])==1


@pytest.mark.parametrize('problem',['invalid_definition','missing_setting'])
def test_invalid_setup_does_not_write_managed_files(project,tmp_path,problem):
    from poise.infrastructure.hook_transport import setup_runtime
    path,packet=prepared(project,tmp_path)
    if problem=='invalid_definition':packet['installation']['definition']['events'][0]['matcher']='['
    else:packet['settings'].pop('file_mode')
    with pytest.raises(PoiseError):setup_runtime(path,packet)
    assert not path.exists() and not (project['root']/'.codex/hooks.json').exists()


def test_setup_cannot_replace_different_existing_settings(project,tmp_path):
    from poise.infrastructure.hook_transport import setup_runtime
    path,packet=prepared(project,tmp_path);setup_runtime(path,packet);old=path.read_bytes()
    changed=deepcopy(packet);changed['settings']['output_chars']+=1
    with pytest.raises(PoiseError):setup_runtime(path,changed)
    assert path.read_bytes()==old


def test_setup_cli_has_explicit_bootstrap_limit(project,tmp_path):
    path,packet=prepared(project,tmp_path)
    result=subprocess.run([sys.executable,'-m','poise','runtime-setup','--settings',str(path),
        '--max-input-bytes',str(1048576)],input=json.dumps(packet),text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['capability_checks']['ready']
