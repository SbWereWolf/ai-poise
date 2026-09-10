import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]/'src/harness'


def test_domains_and_batch_application_no_io():
    files=[ROOT/'modules/artifact_factory/domain.py',ROOT/'modules/interactions/domain.py',ROOT/'application/work.py']
    for path in files:
        for n in ast.walk(ast.parse(path.read_text())):
            modules=[x.name for x in n.names] if isinstance(n,ast.Import) else [n.module or ''] if isinstance(n,ast.ImportFrom) else []
            assert not any(x.split('.')[0] in {'pathlib','sqlite3','os','subprocess','fcntl','time','datetime'} or 'infrastructure' in x for x in modules),(path,modules)


def test_runtime_no_manual_result_file_contract():
    source=(ROOT/'runtime.py').read_text()
    assert 'result_path' not in source
    assert "self.paths['result']" not in source
