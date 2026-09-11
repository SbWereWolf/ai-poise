import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]/'src/poise'


def test_evidence_domain_and_application_do_not_contain_io():
    for p in [(ROOT/'modules/evidence/domain.py'),(ROOT/'application/evidence.py')]:
        for n in ast.walk(ast.parse(p.read_text())):
            names=([a.name for a in n.names] if isinstance(n,ast.Import) else
                   [n.module or ''] if isinstance(n,ast.ImportFrom) else [])
            assert not any(x.split('.')[0] in {'sqlite3','os','subprocess','pathlib','datetime','time'} for x in names)
    source=(ROOT/'runtime.py').read_text()
    assert 'INSERT INTO evidence' not in source and 'UPDATE task_proofs' not in source
    assert 'record_evidence' not in (ROOT/'storage.py').read_text()


def test_observe_check_share_executor_not_goal_type_dispatch():
    handlers=(ROOT/'modules/workflow/handlers.py').read_text()
    assert 'class ObserveHandler' in handlers and 'class CheckHandler' in handlers
    assert 'subprocess' not in handlers and 'sqlite' not in handlers
    assert 'verification_demo' not in handlers and 'profiling' not in handlers
