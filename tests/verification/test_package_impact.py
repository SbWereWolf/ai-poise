"""C017: declared input impact, not a second runner or whole-suite inference."""
import json
from pathlib import Path

import pytest

from poise.common import PoiseError
from poise.infrastructure.test_packages import AiPoiseTestPackages


def package(name, sources, tests, boundaries=(), fixtures=()):
    return {'id': name, 'owner': name + '-owner', 'integration_boundaries': list(boundaries),
            'members': {'source': list(sources), 'tests': list(tests),
                        'fixtures': list(fixtures)}}


@pytest.fixture
def subject(tmp_path):
    copy = tmp_path / 'arbitrary checkout'
    copy.mkdir()
    (copy / 'pyproject.toml').write_text('[project]\nname="ai-poise"\nversion="1"\n')
    raw = {'schema': 'ai-poise-test-packages-2', 'packages': [
        package('domain', ['src/domain/**/*.py'], ['tests/domain/test_*.py'], ['api'],
                ['fixtures/input.json']),
        package('api', ['src/api.py'], ['tests/test_api.py'], ['ui']),
        package('ui', ['src/ui.py'], ['tests/test_ui.py']),
    ]}
    path = tmp_path / 'running-catalog.json'
    path.write_text(json.dumps(raw))
    return AiPoiseTestPackages.load(path), copy, path


def test_deleted_and_new_files_are_matched_from_declarations_without_globbing_current_tree(subject):
    catalog, copy, _ = subject
    paths = ['src/domain/deleted.py', 'src/domain/new/deep.py']
    result = catalog.impact(copy, paths)
    assert result['direct_packages'] == ['domain']
    assert result['integration_packages'] == ['api']
    assert result['suggested_packages'] == ['api', 'domain']
    assert result['unmapped_paths'] == []
    assert [r['path'] for r in result['reasons']] == paths
    assert all(r['kind'] == 'source' and r['owner'] == 'domain-owner' for r in result['reasons'])
    assert result['integration_reasons'] == [{'from_package': 'domain', 'package': 'api'}]
    assert not (copy / '.poise-test-cache').exists()


@pytest.mark.parametrize('path,kind', [
    ('src/domain/new.py', 'source'),
    ('tests/domain/test_new.py', 'tests'),
    ('fixtures/input.json', 'fixtures'),
])
def test_all_three_member_kinds_contribute_to_impact(subject, path, kind):
    catalog, copy, _ = subject
    result = catalog.impact(copy, [path])
    assert result['direct_packages'] == ['domain']
    assert result['reasons'] == [{'path': path, 'package': 'domain', 'owner': 'domain-owner',
                                 'kind': kind, 'pattern': catalog.package('domain').patterns[kind][0]}]


def test_shared_inputs_cover_all_declaring_owners_without_transitive_flood(subject):
    _, copy, path = subject
    raw = json.loads(path.read_text())
    raw['packages'][1]['members']['fixtures'] = ['fixtures/input.json']
    path.write_text(json.dumps(raw))
    result = AiPoiseTestPackages.load(path).impact(copy, ['fixtures/input.json'])
    assert result['direct_packages'] == ['api', 'domain']
    assert result['integration_packages'] == ['ui']
    assert [r['package'] for r in result['reasons']] == ['api', 'domain']


def test_unknown_path_is_a_mapping_gap_even_under_known_source_directory(subject):
    catalog, copy, _ = subject
    result = catalog.impact(copy, ['src/unmapped.py'])
    assert result['unmapped_paths'] == ['src/unmapped.py']
    assert result['suggested_packages'] == []


def test_single_star_does_not_match_arbitrary_subdirectory(subject):
    catalog, copy, _ = subject
    assert catalog.impact(copy, ['tests/domain/nested/test_new.py'])['unmapped_paths'] == [
        'tests/domain/nested/test_new.py']


def test_order_duplicates_and_renames_are_deterministic(subject):
    catalog, copy, _ = subject
    before = catalog.impact(copy, ['src/ui.py', 'src/domain/old.py'])
    assert before == catalog.impact(copy, ['src/domain/old.py', 'src/ui.py', 'src/ui.py'])
    assert catalog.impact(copy, [])['suggested_packages'] == []
    assert catalog.impact(copy, ['src/api.py']) != before


@pytest.mark.parametrize('bad', ['../elsewhere.py', '/tmp/x.py', 'src/**', 'src/a\\b.py', './src/api.py'])
def test_invalid_paths_rejected_before_any_selection(subject, bad):
    catalog, copy, _ = subject
    with pytest.raises(PoiseError):
        catalog.impact(copy, ['src/api.py', bad])


def test_symlink_outside_copy_rejected(subject, tmp_path):
    catalog, copy, _ = subject
    (copy / 'src').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PoiseError):
        catalog.impact(copy, ['src/api.py'])


def test_unrelated_codebase_is_not_forced_to_adopt_poise_cache(subject):
    catalog, copy, _ = subject
    (copy / 'pyproject.toml').write_text('[project]\nname="erp"\n')
    with pytest.raises(PoiseError, match='ai-poise'):
        catalog.impact(copy, [])


def test_selected_packages_feed_existing_cache_and_registered_runner_only(tmp_path):
    from poise.infrastructure.test_package_cache import AiPoiseTestPackageCache
    copy = tmp_path / 'test subject'; copy.mkdir()
    files = {
        'pyproject.toml': '[project]\nname="ai-poise"\nversion="1"\n',
        'src/calc.py': 'def double(n):\n    return n * 2\n',
        'tests/test_calc.py': 'from calc import double\ndef test_double():\n    assert double(3) == 6\n',
        'tests/test_unrelated.py': 'def test_must_not_run():\n    assert False, "unrelated"\n',
    }
    for name, body in files.items():
        path = copy / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body)
    path = copy / 'config/testing/test-packages.json'; path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'schema': 'ai-poise-test-packages-2', 'packages': [
        package('calc', ['src/calc.py'], ['tests/test_calc.py']),
        package('unrelated', [], ['tests/test_unrelated.py']),
    ]}))
    selected = AiPoiseTestPackages.load(path).impact(copy, ['src/calc.py'])
    assert selected['suggested_packages'] == ['calc']
    result = AiPoiseTestPackageCache(copy, path).run(selected['suggested_packages'][0], timeout_seconds=15, fresh=True)
    assert result['passed'] is True and result['executed'] is True
    assert [c['name'] for c in result['cases']] == ['test_double']


def test_impact_domain_does_not_introduce_io_or_runner():
    import ast
    path = Path(__file__).resolve().parents[2] / 'src/poise/modules/verification/impact.py'
    for node in ast.walk(ast.parse(path.read_text())):
        imports = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                   [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
        assert not any('infrastructure' in n or n.split('.')[0] in
                       {'os', 'subprocess', 'pathlib', 'sqlite3'} for n in imports)


def test_pattern_semantics_match_c016_path_glob_for_existing_paths(tmp_path):
    from poise.modules.verification.impact import matches_member
    names = ['src/a.py', 'src/nested/b.py', 'src/nested/deep/c.py', 'src/a.txt']
    for name in names:
        p = tmp_path / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('')
    for pattern in ['src/**/*.py', 'src/*.py', 'src/**/b.py', 'src/**', 'src/[ab].py']:
        from_glob = {p.relative_to(tmp_path).as_posix() for p in tmp_path.glob(pattern) if p.is_file()}
        assert {name for name in names if matches_member(name, pattern)} == from_glob
