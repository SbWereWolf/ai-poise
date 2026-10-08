import json
import os
from pathlib import Path
import subprocess
import sys
from .helpers import enabled
from batch.helpers import request
from conftest import git


def test_transfer_cli_rejects_unknown_operation_without_worktree(project,recovery_tool):
    enabled(project,recovery_tool)
    from conftest import WorkPoise
    WorkPoise(project['config_path'], 'validated-cli-config')
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2]/'src'),
         'POISE_CONFIG':str(project['config_path']),'POISE_CALLER_BINDING':str(project['root']/'transfer-cli.caller.json')}
    p=subprocess.run([sys.executable,'-m','poise','work'],
         input=json.dumps(request('transfer',{'action':'unknown'})),text=True,capture_output=True,env=env)
    assert p.returncode==2 and json.loads(p.stdout)['status']=='rejected'
    assert not (project['root']/'state/worktrees/T1').exists()


def test_transfer_cli_compact_export_through_real_entry_point(project,recovery_tool,tmp_path):
    from .test_paths import prepared
    from .helpers import handoff_args
    from .test_compact_recovery import archive_contents
    runtime, tools, context, payload = prepared(project,recovery_tool)
    tools.invoke(request('handoff', handoff_args(payload)))
    environment = {**os.environ,
        'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src'),
        'POISE_CONFIG': str(project['config_path']),
        'POISE_CALLER_BINDING': str(project['root'] / 'compact-cli.caller.json')}
    packet = request('transfer', {'action':'export','request_id':'cli-export',
        'task_ids':['T1'],'sprint_id':None,'handoff':None})
    process = subprocess.run([sys.executable,'-B','-m','poise','work'],
        input=json.dumps(packet),text=True,capture_output=True,env=environment)
    assert process.returncode == 0, process.stdout + process.stderr
    output = json.loads(process.stdout)
    if output.get('details') == 'full_result':
        output = json.loads(Path(output['response_path']).read_text())
    assert output['status'] == 'exported'
    entries, manifest = archive_contents(output)
    assert manifest['format'] == 'poise-recovery-1'
    assert manifest['workspaces']['T1']['commit'] == git(Path(context['worktree']),'rev-parse','HEAD')
    assert not any(data.startswith(b'# v2 git bundle\n') for data in entries.values())
    assert runtime.current_task() is None


def test_transfer_cli_end_to_end_demo(tmp_path,recovery_tool):
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / 'demo'
    process = subprocess.run([
        sys.executable, str(root / 'examples/transfer_demo.py'),
        '--directory', str(target), '--recovery-node', recovery_tool[0],
        '--recovery-tool', recovery_tool[1],
    ],env={**os.environ,'PYTHONPATH':str(root/'src')},text=True,capture_output=True)
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads((target/'transfer-demo-report.json').read_text())
    assert report['final_status'] == 'completed' and report['reports'] == 4
    assert report['red_exit'] == 1 and report['green_exit'] == 0
    # These are new local checks after delivery, not transferred old logs.
    assert report['full_output_bytes_after_acceptance'] > 0
    assert report['terminal_read_did_not_claim_task']
    from .test_compact_recovery import archive_contents
    _, manifest = archive_contents(report['export'])
    assert manifest['format'] == 'poise-recovery-1'
