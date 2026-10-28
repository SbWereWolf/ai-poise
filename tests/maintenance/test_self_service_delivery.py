"""Late tracked-example and canonical documentation checks, never product RED."""
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_shipped_profile_uses_operation_configs_and_ignored_working_files():
    spec = ROOT / 'config/maintenance/start-app/profile.example.json'
    assert spec.is_file()
    app = ROOT / 'config/maintenance/start-app/app.example.json'
    value = json.loads(app.read_text())
    assert {'infra', 'deps', 'check', 'run'} <= set(value['operations'])
    for path in (spec, app):
        checked = subprocess.run(['git', 'ls-files', '--error-unmatch', '--', str(path.relative_to(ROOT))],
                                 cwd=ROOT, capture_output=True)
        assert checked.returncode == 0, checked.stderr
        assert '/home/sbwerewolf/' not in path.read_text()
    ignored = subprocess.run(['git', 'check-ignore', '--no-index', 'operator/profile/parameters.json'],
                             cwd=ROOT, capture_output=True)
    assert ignored.returncode == 0
    assert subprocess.check_output(['git', 'ls-files', '--', 'operator/profile/'], cwd=ROOT) == b''


def test_canonical_docs_use_the_existing_checker():
    paths = ['docs/operator/bootstrap-catalog-v2.md', 'docs/operator/maintenance-profile-v2.md',
             'docs/operator/poise-command-reference.md', '.agents/skills/poise/SKILL.md']
    argv = [sys.executable, '-B', 'tools/check_documentation.py', '--checkout', str(ROOT)]
    for path in paths:
        argv.extend(['--changed', path])
    result = subprocess.run(argv, cwd=ROOT, capture_output=True)
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_poise_skill_links_to_its_canonical_setup_near_heading():
    lines = (ROOT / '.agents/skills/poise/SKILL.md').read_text().splitlines()
    matches = [i for i, line in enumerate(lines)
               if 'maintenance-profile-v2.md#развёртывание-из-примера' in line]
    assert matches
    for index in matches:
        headings = [i for i in range(index + 1) if lines[i].startswith('#')]
        assert headings and index - headings[-1] <= 10
