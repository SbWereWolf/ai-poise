"""Standard isolated package consumption with retained raw process evidence."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

from hook_transport.preparation_source_upgrade_support import (
    FIXTURES, ROOT, clean_environment, copy_candidate, read_json, source_inventory,
)


def output_directory(tmp_path):
    selected = os.environ.get('POISE_RUN_OUTPUT_DIR')
    root = Path(selected) if selected is not None else tmp_path / 'proof'
    target = root / 'source-composition'
    target.mkdir(parents=True, exist_ok=True)
    return target


def installed_probe(tmp_path):
    wheel_input = Path(os.environ['AIP_P004_SOURCE_SUPPLY_WHEEL'])
    supply = wheel_input.parent
    acquisition = read_json(Path(os.environ['AIP_P004_SOURCE_SUPPLY_RECEIPT']))
    assert wheel_input.name == acquisition['wheel']['filename']
    assert hashlib.sha256(wheel_input.read_bytes()).hexdigest() == acquisition['wheel']['sha256']
    assert acquisition['wheel']['name'] == 'psutil'
    assert acquisition['capture_owner']['task'] == 'ERP-HARNESS-P004'
    assert acquisition['exit_code'] == 0
    for name, variable in [('download.stdout', 'AIP_P004_SOURCE_SUPPLY_STDOUT'),
                           ('download.stderr', 'AIP_P004_SOURCE_SUPPLY_STDERR')]:
        assert hashlib.sha256(Path(os.environ[variable]).read_bytes()).hexdigest() == acquisition['streams'][name]['sha256']
    proof = output_directory(tmp_path)
    root = tmp_path / 'isolated package consumer Ω'
    candidate = copy_candidate(root / 'candidate')
    environment = root / 'environment'
    commands = []
    clean = clean_environment()
    clean['HOME'] = str(root / 'home')
    Path(clean['HOME']).mkdir()

    def run(argv, name):
        result = subprocess.run(argv, cwd=root, env=clean, capture_output=True, check=False)
        (proof / (name + '.stdout')).write_bytes(result.stdout)
        (proof / (name + '.stderr')).write_bytes(result.stderr)
        commands.append({'name': name, 'argv': [str(a) for a in argv], 'cwd': str(root),
                         'exit_code': result.returncode})
        assert result.returncode == 0, (name, result.stdout, result.stderr)
        return result

    wheel_dir = root / 'wheels'
    run([sys.executable, '-B', '-m', 'pip', 'wheel', '--no-index', '--no-deps',
         '--no-build-isolation', '--wheel-dir', str(wheel_dir), str(candidate)], 'build')
    candidate_wheel, = wheel_dir.glob('*.whl')
    shutil.copyfile(candidate_wheel, proof / 'candidate.whl')
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(environment)
    python = environment / 'bin/python'
    run([str(python), '-I', '-B', '-m', 'pip', 'install', '--no-index',
         '--find-links', str(supply), str(candidate_wheel)], 'install')
    run([str(python), '-I', '-B', '-m', 'pip', 'check'], 'pip-check')
    result = run([str(python), '-I', '-B', str(FIXTURES / 'import_probe.py')], 'consumer')
    manifest = {'source_inventory': source_inventory(ROOT),
                'head_at_run': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                'python': sys.version, 'build_python': sys.executable,
                'candidate_filename': candidate_wheel.name,
                'candidate_sha256': hashlib.sha256(candidate_wheel.read_bytes()).hexdigest(),
                'dependency': acquisition, 'commands': commands,
                'environment': {'PYTHONPATH': None, 'PYTHONHOME': None, 'system_site_packages': False}}
    (proof / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return json.loads(result.stdout), environment
