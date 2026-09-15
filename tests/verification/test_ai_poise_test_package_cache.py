import json
from pathlib import Path
import shutil

from poise.infrastructure.test_package_cache import AiPoiseTestPackageCache


def checkout(tmp_path: Path) -> tuple[Path,Path]:
    root=tmp_path/'ai-poise'
    (root/'src/pkg').mkdir(parents=True)
    (root/'tests').mkdir()
    (root/'tools').mkdir()
    (root/'fixtures').mkdir()
    (root/'config/testing').mkdir(parents=True)
    (root/'pyproject.toml').write_text('[project]\nname="ai-poise"\nversion="1"\n')
    (root/'src/pkg/value.py').write_text('VALUE=1\n')
    (root/'tests/test_value.py').write_text('from pkg.value import VALUE\ndef test_value(): assert VALUE == 1\n')
    (root/'tools/helper.py').write_text('HELPER=True\n')
    (root/'fixtures/data.json').write_text('{"x":1}\n')
    catalog=root/'config/testing/test-packages.json'
    catalog.write_text(json.dumps({
        'schema':'ai-poise-test-packages-1',
        'packages':[{
            'id':'sample','owner':'verification','integration_boundaries':[],
            'members':{
                'source':['src/pkg/*.py'],
                'tests':['tests/test_*.py'],
                'support':['tools/*.py'],
                'fixtures':['fixtures/*.json'],
            },
        }],
    }))
    return root,catalog


def test_fingerprint_is_membership_and_content_only_and_relocatable(tmp_path):
    root,catalog=checkout(tmp_path)
    cache=AiPoiseTestPackageCache(root,catalog)
    first=cache.fingerprint('sample')
    moved=tmp_path/'elsewhere'
    shutil.copytree(root,moved)
    second=AiPoiseTestPackageCache(moved,moved/'config/testing/test-packages.json').fingerprint('sample')
    assert first == second
    assert all(not Path(row['path']).is_absolute() for rows in first['content'].values() for row in rows)
    assert 'runtime' not in first and 'environment' not in first and 'command' not in first
    (moved/'fixtures/data.json').write_text('{"x":2}\n')
    assert AiPoiseTestPackageCache(moved,moved/'config/testing/test-packages.json').fingerprint('sample')['input_fingerprint'] != first['input_fingerprint']


def test_success_is_reused_with_origin_provenance_and_fresh_bypasses_cache(tmp_path):
    root,catalog=checkout(tmp_path)
    cache=AiPoiseTestPackageCache(root,catalog)
    first=cache.run('sample',timeout_seconds=30)
    assert first['passed'] and first['executed'] and not first['reused']
    assert first['stdout'].startswith('.poise-test-cache/')
    origin=first['origin_run_id']
    second=cache.run('sample',timeout_seconds=30)
    assert second['passed'] and second['reused'] and not second['executed']
    assert second['origin_run_id'] == origin
    assert second['input_fingerprint'] == first['input_fingerprint']
    assert second['cache_record'].startswith('.poise-test-cache/')
    third=cache.run('sample',timeout_seconds=30,fresh=True)
    assert third['passed'] and third['executed'] and not third['reused']
    assert third['origin_run_id'] != origin
    assert third['input_fingerprint'] == first['input_fingerprint']


def test_changed_package_input_causes_new_execution_not_stale_reuse(tmp_path):
    root,catalog=checkout(tmp_path)
    cache=AiPoiseTestPackageCache(root,catalog)
    first=cache.run('sample',timeout_seconds=30)
    (root/'src/pkg/value.py').write_text('VALUE=2\n')
    (root/'tests/test_value.py').write_text('from pkg.value import VALUE\ndef test_value(): assert VALUE == 2\n')
    second=cache.run('sample',timeout_seconds=30)
    assert second['executed'] and not second['reused']
    assert second['input_fingerprint'] != first['input_fingerprint']
    assert second['origin_run_id'] != first['origin_run_id']
