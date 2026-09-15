"""C027.3: explicit Markdown link/anchor contracts, no formatting policy."""
from pathlib import Path
import pytest
from poise.infrastructure.documentation_checks import check_links


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path


def test_missing_anchor_in_existing_file_is_a_failure(tmp_path):
    source=write(tmp_path/'entry.md','[changed title](page.md#previous-heading)\n')
    write(tmp_path/'page.md','# New heading\n')
    result=check_links(tmp_path,[source])
    assert [e['kind'] for e in result['errors']]==['missing_anchor']
    assert result['errors'][0]['source']=='entry.md'
    assert result['errors'][0]['line']==1


def test_unicode_code_heading_explicit_ids_setext_and_duplicates(tmp_path):
    target=write(tmp_path/'page.md',
        '# Разрешение путей и рабочие копии\n'
        '## `run` &amp; **check**!\n'
        '## Repeat\n## Repeat\n'
        'Setext title\n------------\n'
        '<a id="explicit-id"></a>\n')
    source=write(tmp_path/'entry.md',
        '[a](page.md#разрешение-путей-и-рабочие-копии)\n'
        '[b](page.md#run--check)\n[c](page.md#repeat-1)\n'
        '[d](page.md#setext-title)\n[e](page.md#explicit-id)\n'
        '[f](page.md#%D1%80%D0%B0%D0%B7%D1%80%D0%B5%D1%88%D0%B5%D0%BD%D0%B8%D0%B5-%D0%BF%D1%83%D1%82%D0%B5%D0%B9-%D0%B8-%D1%80%D0%B0%D0%B1%D0%BE%D1%87%D0%B8%D0%B5-%D0%BA%D0%BE%D0%BF%D0%B8%D0%B8)\n')
    result=check_links(tmp_path,[source,target])
    assert result['errors']==[]
    assert len(result['edges'])==6


@pytest.mark.parametrize('body', [
    '```md\n# Not a heading\n```\n',
    '    # Not a heading\n',
    '<!-- # Not a heading -->\n',
])
def test_examples_and_comments_do_not_create_anchors(tmp_path,body):
    source=write(tmp_path/'entry.md','[wrong](page.md#not-a-heading)\n')
    write(tmp_path/'page.md',body)
    assert [e['kind'] for e in check_links(tmp_path,[source])['errors']]==['missing_anchor']


def test_reference_links_spaces_parentheses_and_fences(tmp_path):
    write(tmp_path/'page (one).md','# Good\n')
    source=write(tmp_path/'entry.md',
        '[first][ref]\n[ref]: <page (one).md#good> "Optional title"\n'
        '[first][]\n[first]: page%20(one).md#good\n'
        '[inline](page%20(one).md#wrong)\n'
        '```md\n[ignored](no.md)\n```\n')
    result=check_links(tmp_path,[source])
    assert len(result['edges'])==3
    assert [e['kind'] for e in result['errors']]==['missing_anchor']
    assert result['errors'][0]['line']==5


def test_same_document_fragment_is_checked(tmp_path):
    source=write(tmp_path/'entry.md','# Exists\n[good](#exists)\n[bad](#missing)\n')
    assert [e['kind'] for e in check_links(tmp_path,[source])['errors']]==['missing_anchor']


def test_incoming_links_to_changed_and_removed_targets_are_not_missed(tmp_path):
    from poise.infrastructure.documentation_checks import audit_documentation
    write(tmp_path/'entry.md','[changed](docs/changed.md#old)\n[deleted](docs/deleted.md)\n')
    write(tmp_path/'docs/changed.md','# New\n')
    write(tmp_path/'other.md','[unrelated](unrelated-missing.md)\n')
    report=audit_documentation(tmp_path,['docs/changed.md','docs/deleted.md'])
    assert report['status']=='invalid'
    assert {e['kind'] for e in report['errors']}=={'missing_anchor','missing_file'}
    assert {e['source'] for e in report['errors']}=={'entry.md'}
    assert {e['target_file'] for e in report['errors']}=={'docs/changed.md','docs/deleted.md'}
    assert audit_documentation(tmp_path,[str(tmp_path/'docs')])['errors']==report['errors']


def test_changed_document_outgoing_links_are_checked_and_foreign_path_rejected(tmp_path):
    from poise.infrastructure.documentation_checks import audit_documentation
    write(tmp_path/'entry.md','[bad](gone.md)\n')
    result=audit_documentation(tmp_path,['entry.md'])
    assert result['errors'][0]['kind']=='missing_file'
    with pytest.raises(ValueError,match='escapes'):
        audit_documentation(tmp_path,['../foreign'])


def test_external_and_non_markdown_fragments_are_explicitly_unchecked(tmp_path):
    write(tmp_path/'data.json','{}')
    source=write(tmp_path/'entry.md',
        '[web](https://example.invalid/no-access)\n[data](data.json#not-validated)\n')
    result=check_links(tmp_path,[source])
    assert result['errors']==[]
    assert {r['kind'] for r in result['skipped']}=={
        'external_link_not_checked','non_markdown_fragment_not_checked'}


@pytest.mark.parametrize('relative,fragment', [
    ('docs/migrations/erp-runtime-migration-retrospective-2026-09-15.md',
     'разрешение-путей-и-рабочие-копии'),
    ('docs/workflows/sprints.md','замкнутость-зависимостей-и-локальные-дубли'),
    ('docs/workflows/checkpoint-recovery.md','контрольная-точка-и-восстановление'),
])
def test_exact_policy_links_resolve_and_renamed_heading_is_detected(tmp_path,relative,fragment):
    from poise.infrastructure.documentation_checks import links,document_anchors
    checkout=Path(__file__).resolve().parents[2]
    original=(checkout/relative).read_text()
    assert fragment in document_anchors(original)
    rule=(checkout/'AGENTS.md').read_text()
    assert any(link.target==relative+'#'+fragment for link in links(rule))
    write(tmp_path/relative,original)
    source=write(tmp_path/'rule.md',f'[Exact policy]({relative}#{fragment})\n')
    assert check_links(tmp_path,[source])['errors']==[]
    # Only the copied target is changed; real repository state is untouched.
    write(tmp_path/relative,'# A different heading\n')
    assert check_links(tmp_path,[source])['errors'][0]['kind']=='missing_anchor'


def test_shipped_current_documentation_has_no_broken_files_or_anchors():
    from poise.infrastructure.documentation_checks import audit_documentation
    result=audit_documentation(Path(__file__).resolve().parents[2])
    assert result['status']=='valid',result['errors']
