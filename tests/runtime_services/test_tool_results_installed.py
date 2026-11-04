"""A genuine noneditable wheel, complete membership, and public consumers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

from tests.runtime_services.tool_results_support import FIXTURES, ROOT, REMOVED, Scenario, literal, sha, wire, write_json


def test_installed_tool_result_consumer(project, tmp_path):
    evidence = Path(os.environ['POISE_RUN_OUTPUT_DIR']) / 'installed'
    evidence.mkdir()
    work = evidence / 'source'; work.mkdir()
    for name in ('src', 'apps'):
        shutil.copytree(ROOT / name, work / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(ROOT / 'pyproject.toml', work / 'pyproject.toml')
    env = {k: v for k, v in os.environ.items() if k not in REMOVED}
    env.update(PYTHONDONTWRITEBYTECODE='1', PIP_CACHE_DIR=str(evidence / 'pip-cache'), TMPDIR=str(evidence))
    proof = {'native_receipt': False, 'commands': [], 'members': [], 'tool_calls': [],
             'consumer_complete': False}

    def save(): write_json(evidence / 'proof.json', proof)

    def run(argv):
        cp = subprocess.run(argv, cwd=evidence, env=env, capture_output=True, check=False)
        proof['commands'].append({'argv': argv, 'cwd': str(evidence), 'exit_code': cp.returncode,
                                  'stdout': cp.stdout.decode(), 'stderr': cp.stderr.decode()})
        save()
        assert cp.returncode == 0, proof['commands'][-1]
        return cp.stdout

    wheels = evidence / 'wheels'; wheels.mkdir()
    run([sys.executable, '-B', '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--no-index',
         '--wheel-dir', str(wheels), str(work)])
    wheel, = wheels.glob('*.whl')
    shutil.copy2(wheel, evidence / 'package.whl')
    venv = evidence / 'venv'
    run([sys.executable, '-B', '-m', 'venv', '--without-pip', str(venv)])
    python = str(venv / 'bin/python')
    run([sys.executable, '-B', '-m', 'pip', '--python', python, 'install', '--no-deps',
         '--no-index', str(wheel)])
    identity = json.loads(run([python, '-I', '-B', '-c',
        'import json,sys,poise,importlib.metadata as m; d=m.distribution("ai-poise");'
        'print(json.dumps({"module":poise.__file__,"prefix":sys.prefix,"version":d.version,'
        '"direct_url":d.read_text("direct_url.json")}))']))
    assert Path(identity['module']).is_relative_to(venv)
    assert not Path(identity['module']).is_relative_to(ROOT)
    assert not json.loads(identity['direct_url']).get('dir_info', {}).get('editable', False)
    assert identity['version'] == tomllib.loads((work / 'pyproject.toml').read_text())['project']['version']
    site = Path(identity['module']).parent.parent
    sources = {str(p.relative_to(ROOT / 'src')): p.read_bytes()
               for package in ('poise', 'poise_environment')
               for p in (ROOT / 'src' / package).rglob('*')
               if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    sources.update({str(p.relative_to(ROOT)): p.read_bytes()
                    for p in (ROOT / 'apps/bootstrap').rglob('*')
                    if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'})
    with zipfile.ZipFile(wheel) as archive:
        members = {n: archive.read(n) for n in archive.namelist()
                   if n.startswith(('poise/', 'poise_environment/', 'apps/bootstrap/')) and not n.endswith('/')}
    assert set(members) == set(sources)
    for name, raw in sorted(sources.items()):
        assert members[name] == raw == (site / name).read_bytes()
        proof['members'].append({'path': name, 'bytes': len(raw), 'sha256': sha(raw)})
    proof.update(identity=identity, wheel=str(wheel), wheel_sha256=sha(wheel.read_bytes()),
                 membership_complete=True)
    save()
    for kind in ('bash', 'mcp'):
        parent = tmp_path / kind; parent.mkdir()
        s = Scenario(project, parent, python=python, source=False)
        if kind == 'mcp':
            cp = subprocess.run([sys.executable, '-B', str(FIXTURES / 'producer.py'),
                                 '--kind', 'mcp', '--size', 'short', '--counter',
                                 str(s.producer_count), '--exit-code', '0'], capture_output=True)
            assert cp.returncode == 0 and cp.stderr == b''
            expected = literal('mcp-response.json')
            assert wire(json.loads(cp.stdout)) == wire(expected)
            req = s.request(tool={'kind': kind, 'name': 'installed-fixture'}, response=expected,
                            streams={'stdout': None, 'stderr': None}, exit_code=None)
        else:
            s.bash_producer()
            expected = s.request()['response']
            req = s.request(tool={'kind': kind, 'name': 'installed-fixture'}, response=expected)
        try:
            _, saved = s.capture(req, 'machine')
            shown = s.success(s.request('view.template.json'))
            assert 'response_path' not in shown
            full = s.success(s.request('view.template.json', view='full'))
            assert full['response_path'] == str(s.response_path)
            assert saved['response'] == expected
            if kind == 'mcp':
                oracle = literal('mcp-reader-expected.json')
                assert saved['streams'] == {'stdout': None, 'stderr': None}
                read = s.success(s.request('read.template.json', channel='response', **oracle['request_lines']))
                assert read['text'] == oracle['text'] and read['returned_lines'] == oracle['returned_lines']
                search = s.success(s.request('search.template.json', channel='response', **oracle['request_search']))
                assert search['matches'] == oracle['matches'] and search['lines'] == oracle['context']
                assert read['channel'] == search['channel'] == 'response'
            else:
                assert s.success(s.request('search.template.json', pattern='FIRST_STDOUT'))['matches']
                assert s.success(s.request('read.template.json', from_line=1, to_line=2))['text'] == 'FIRST_STDOUT\nshort\n'
            assert s.producer_count.read_text() == '1'
        finally:
            proof['tool_calls'].extend(s.calls); save()
    proof['consumer_complete'] = True
    save()
