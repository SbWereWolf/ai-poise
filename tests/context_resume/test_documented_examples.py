"""Late documentation evidence: real examples and the existing link checker."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from tests.context_resume.helpers import Recovery, card, fixture


ROOT = Path(__file__).resolve().parents[2]


def documented_options(name):
    text = (ROOT / 'docs/workflows/context-recovery.md').read_text(encoding='utf-8')
    pattern = r'<!-- f06-example: ' + re.escape(name) + r' -->\s*```json\s*\n(.*?)\n```'
    blocks = re.findall(pattern, text, re.DOTALL)
    assert len(blocks) == 1, 'A real complete canonical example is required'
    return json.loads(blocks[0])


@pytest.mark.parametrize('name', ['start-compact', 'resume-clear'])
def test_documented_context(name, project, tmp_path, monkeypatch):
    example = documented_options(name)
    assert example['events'] == fixture()['native_events']
    # Configuration input is the actual documented object; the expected behavior is literal.
    assert example['general_context'] == fixture()['general_context']
    r = Recovery(project, tmp_path, monkeypatch).enable(example=example)
    r.save(); r.compact()
    value = r.restore()
    assert value['generation'] == {'before': 1, 'current': 2}
    if name == 'start-compact':
        assert value['checkpoint']['state'] == 'restored'
        assert r.restore('resume')['checkpoint']['state'] == 'used'
    else:
        assert r.restore('clear')['checkpoint']['state'] == 'missing'
        assert not r.checkpoint.exists()
    target = r.s.resource
    r.event('SessionEnd')
    assert r.s.invoke({'operation': 'recover'})[1]['status'] == 'cleaned'
    assert not target.exists()


def test_changed_documentation_links():
    # Git supplies changed files. This adapter never parses or scans document links.
    base = '55035c994770c0dc03e30492f16a54e0e4c05528'
    tracked = subprocess.check_output(['git', 'diff', '--name-only', '-z', base], cwd=ROOT)
    new = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=ROOT)
    changed = sorted({p.decode('utf-8') for p in (tracked + new).split(b'\0') if p})
    assert changed, 'This late method proves an actual delivered documentation result'
    checker = os.environ['POISE_DOC_CHECKER']
    argv = [sys.executable, '-P', '-B', checker, '--checkout', str(ROOT)]
    for path in changed:
        argv.extend(['--changed', path])
    cp = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, check=False)
    print(cp.stdout, end=''); print(cp.stderr, end='', file=sys.stderr)
    assert cp.returncode == 0, (argv, cp.stdout, cp.stderr)
    result = json.loads(cp.stdout)
    assert result['status'] == 'valid' and result['errors'] == []
