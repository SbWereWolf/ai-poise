"""Explicit disposable system/config companions owned by check execution tests."""
import json
from pathlib import Path
import subprocess


def prepare_check_systems(root, repository, config_path):
    installed = root / 'installed-poise'
    installed.mkdir()
    for args in [('init', '-b', 'main'), ('config', 'user.name', 'Check fixture'),
                 ('config', 'user.email', 'check@example.invalid')]:
        subprocess.run(['git', '-C', str(installed), *args], check=True, capture_output=True)
    (installed / 'version').write_text('installed current system\n')
    subprocess.run(['git', '-C', str(installed), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(installed), 'commit', '-m', 'Installed check system fixture'],
                   check=True, capture_output=True)
    fixture = Path(__file__).parent / 'runtime_services/fixtures/check_system_configs.json'
    for name, value in json.loads(fixture.read_text()).items():
        (root / (name + '.json')).write_text(json.dumps(value) + '\n')
    return {
        'poise': {'repository': str(installed),
                  'configs': [str(config_path), str(root / 'poise_overlay.json')]},
        'project': {'repository': str(repository),
                    'configs': [str(root / 'project_config.json'), str(root / 'project_overlay.json')]},
    }
