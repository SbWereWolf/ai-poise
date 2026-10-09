"""One explicit test-owned recovery arrangement, independent of product parsing."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess


FIXTURES = Path(__file__).parent / 'transfer' / 'fixtures'


def tiny_repository(path):
    path.mkdir()
    for args in [('init', '-b', 'main'), ('config', 'user.name', 'Recovery fixture'),
                 ('config', 'user.email', 'recovery@example.invalid')]:
        subprocess.run(['git', '-C', str(path), *args], check=True, capture_output=True)
    (path / 'version').write_text('installed current system\n')
    subprocess.run(['git', '-C', str(path), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(path), 'commit', '-m', 'Installed system fixture'],
                   check=True, capture_output=True)


def prepare_recovery_files(root):
    tiny_repository(root / 'installed-poise')
    configs = json.loads((FIXTURES / 'compact-system-configs.json').read_text())
    for name, value in configs.items():
        (root / (name + '.json')).write_text(json.dumps(value) + '\n')
    shutil.copyfile(FIXTURES / 'unexpected_flow.py', root / 'unexpected_flow.py')


def recovery_policy(root, repository, config_path, tool_argv):
    replacements = {
        '@POISE_REPOSITORY@': str(root / 'installed-poise'),
        '@POISE_CONFIG@': str(config_path),
        '@POISE_OVERLAY@': str(root / 'poise_overlay.json'),
        '@PROJECT_REPOSITORY@': str(repository),
        '@PROJECT_CONFIG@': str(root / 'project_config.json'),
        '@PROJECT_OVERLAY@': str(root / 'project_overlay.json'),
    }
    def bind(value):
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        if isinstance(value, list):
            return [bind(item) for item in value]
        return replacements.get(value, value) if isinstance(value, str) else value
    raw = json.loads((FIXTURES / 'compact-recovery-policy.json').read_text())
    raw['tool_argv'] = list(tool_argv)
    return bind(raw)


def recovery_settings(root, repository, config_path, tool_argv):
    raw = json.loads((FIXTURES / 'compact-transfer-settings.json').read_text())
    raw['recovery'] = recovery_policy(root, repository, config_path, tool_argv)
    return deepcopy(raw)
