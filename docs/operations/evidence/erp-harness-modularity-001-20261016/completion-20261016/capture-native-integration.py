"""Copy one actual terminal native integration batch and verify its stream hashes."""
from pathlib import Path
import hashlib
import json
import shutil
import sys

response = Path(sys.argv[1])
destination = Path(sys.argv[2])
owner = Path('/home/sbwerewolf/workdata/poise-project-data/erp/standalones/ERP-HARNESS-MODULARITY-001/runs')
subject = Path('/home/sbwerewolf/workdata/erp/worktrees/ERP-HARNESS-MODULARITY-001')
data = json.loads(response.read_bytes())
checks = [check for check in data['checks'] if check['integration_head'] == data['integration_head']]
assert len(checks) == 10
streams_only = sys.argv[3:] == ['--native-streams-only']
assert streams_only or sys.argv[3:] == []
assert not destination.exists()
destination.mkdir()
shutil.copyfile(response, destination / 'response.json')
for check in checks:
    folder = owner / check['id']
    assert Path(check['stdout']).parent == folder
    assert Path(check['stderr']).parent == folder
    shutil.copytree(folder, destination / check['id'])
    for stream in ('stdout', 'stderr'):
        copied = destination / check['id'] / Path(check[stream]).name
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == check[stream + '_digest']
    if not streams_only and check['method'] in (
        'MODULARITY-REGRESSION-RUN-CHECK',
        'MODULARITY-GATE-PROVIDER-BINDING-VERIFICATION',
        'MODULARITY-GATE-AGENT-TOOLING-FAST-VERIFICATION',
        'MODULARITY-DOCUMENTATION-EXACT-SCOPE',
    ):
        result = json.loads(Path(check['stdout']).read_bytes())
        if 'result_kind' in result:
            assert result['result_kind'] == 'overflow'
            record = subject / result['recovery_path']
        else:
            record = subject / result['artifacts']['record_path']
        assert record.resolve().is_relative_to((subject / '.runtime/checks').resolve())
        shutil.copytree(record.parent.parent, destination / 'registered' / result['check_id'])
files = [{'path': str(path.relative_to(destination)),
          'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
         for path in sorted(destination.rglob('*')) if path.is_file()]
(destination / 'manifest.json').write_text(json.dumps({'files': files}, indent=2) + '\n')
for item in files:
    assert hashlib.sha256((destination / item['path']).read_bytes()).hexdigest() == item['sha256']
print(json.dumps({'status': data['status'], 'native_checks': len(checks),
                  'passed': sum(check['passed'] for check in checks),
                  'verified_streams': 20, 'copied_files': len(files),
                  'nested_registered_records_copied': not streams_only}))
