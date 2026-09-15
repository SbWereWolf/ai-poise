"""C027.4 independent expectations for the retained ERP formatting contract."""
import os
from pathlib import Path
import pytest
from poise.common import PoiseError
from poise.infrastructure.markdown_format import format_markdown, format_files


@pytest.mark.parametrize('eol', ['\n', '\r', '\r\n'])
def test_eol_and_wrap_preserve_terminal_newline(eol):
    assert format_markdown('one two three four\r\n', 9, eol) == 'one two' + eol + 'three' + eol + 'four' + eol
    assert format_markdown('one two three', 9, eol) == 'one two' + eol + 'three'


def test_frontmatter_tables_headings_and_long_fence_are_not_wrapped():
    source = '---\ntitle: a very long front matter value\n---\n# a very long heading\n| a very long table | value |\n````py\n```\na long code line still inside the fence\n````\nplain prose wraps here\n'
    expected = '---\ntitle: a very long front matter value\n---\n# a very long heading\n| a very long table | value |\n````py\n```\na long code line still inside the fence\n````\nplain\nprose\nwraps\nhere\n'
    assert format_markdown(source, 7, '\n') == expected


def test_list_indent_hardbreak_and_indented_code_preserved():
    source = '- one two three four  \n    long indented code must remain unchanged\n'
    assert format_markdown(source, 11, '\n') == '- one two\n  three\n  four  \n    long indented code must remain unchanged\n'


def setup_copy(tmp_path):
    (tmp_path/'pyproject.toml').write_text('[project]\nname="ai-poise"\n')
    file=tmp_path/'document.md';file.write_bytes(b'one two three four\r\n')
    return file


def test_check_is_readonly_fix_is_idempotent_and_preserves_mode(tmp_path):
    p=setup_copy(tmp_path);p.chmod(0o640);before=p.stat()
    checked=format_files(tmp_path,[str(p)],9,'LF','check','IDE is not exposed')
    assert checked['status']=='drift' and p.read_bytes()==b'one two three four\r\n'
    assert p.stat().st_mtime_ns==before.st_mtime_ns
    fixed=format_files(tmp_path,[str(p)],9,'LF','fix','IDE is not exposed')
    assert fixed['changed']==[str(p)]
    assert p.read_bytes()==b'one two\nthree\nfour\n'
    assert p.stat().st_mode & 0o777 == 0o640
    after=p.stat()
    assert format_files(tmp_path,[str(p)],9,'LF','fix','IDE is not exposed')['changed']==[]
    assert p.stat().st_mtime_ns==after.st_mtime_ns and p.stat().st_ino==after.st_ino


def test_invalid_late_file_rejects_entire_batch_before_any_write(tmp_path):
    p=setup_copy(tmp_path);before=p.read_bytes()
    with pytest.raises(PoiseError):format_files(tmp_path,[str(p),str(tmp_path/'missing.md')],9,'LF','fix','No IDE')
    assert p.read_bytes()==before


@pytest.mark.parametrize('field,value', [('mode','unknown'),('eol','unknown'),('width',0),('fallback_reason','')])
def test_invalid_contract_does_not_mutate(tmp_path,field,value):
    p=setup_copy(tmp_path);args={'checkout':tmp_path,'files':[str(p)],'width':9,'eol':'LF','mode':'fix','fallback_reason':'No IDE'}
    args[field]=value
    with pytest.raises(PoiseError):format_files(**args)
    assert p.read_bytes()==b'one two three four\r\n'


def test_foreign_project_or_escaped_path_not_accepted(tmp_path):
    p=setup_copy(tmp_path);other=tmp_path/'other';other.mkdir()
    (other/'pyproject.toml').write_text('[project]\nname="erp"\n')
    with pytest.raises(PoiseError):format_files(other,[str(p)],9,'LF','fix','No IDE')


def test_relative_filename_uses_actual_process_cwd(tmp_path, monkeypatch):
    p=setup_copy(tmp_path);monkeypatch.chdir(tmp_path)
    assert format_files(tmp_path,['document.md'],9,'LF','fix','No IDE')['changed']==[str(p)]
    assert Path.cwd()==tmp_path


def test_symlink_is_not_replaced(tmp_path):
    p=setup_copy(tmp_path);link=tmp_path/'link.md';link.symlink_to(p)
    with pytest.raises(PoiseError):format_files(tmp_path,[str(link)],9,'LF','fix','No IDE')
    assert link.is_symlink() and p.read_bytes()==b'one two three four\r\n'


def test_cli_check_fix_and_files_from(tmp_path):
    import subprocess, sys
    p=setup_copy(tmp_path);names=tmp_path/'selected.txt';names.write_text(str(p)+'\n')
    repository=Path(__file__).resolve().parents[2]
    env={**os.environ,'PYTHONPATH':str(repository/'src')}
    argv=[sys.executable,'-B',str(repository/'tools/format_markdown.py'),'--checkout',str(tmp_path),
          '--files-from',str(names),'--len','9','--eol','CRLF','--fallback-reason','IDE unavailable']
    assert subprocess.run(argv+['--lint-mode','check'],env=env,capture_output=True).returncode==1
    assert subprocess.run(argv+['--lint-mode','fix'],env=env,capture_output=True).returncode==0
    assert p.read_bytes()==b'one two\r\nthree\r\nfour\r\n'
    assert subprocess.run(argv+['--lint-mode','check'],env=env,capture_output=True).returncode==0
    assert subprocess.run(argv,env=env,capture_output=True).returncode==2
