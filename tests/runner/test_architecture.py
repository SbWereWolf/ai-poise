"""Architectural seams of the new runner and pure strategies."""
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]/"src/harness"

def test_workflow_and_inspection_are_pure():
    forbidden={"sqlite3","os","pathlib","subprocess","time","datetime","fcntl"}
    for folder in ("workflow","inspection"):
        for path in (ROOT/"modules"/folder).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names=[]
                if isinstance(node,ast.Import): names=[x.name for x in node.names]
                elif isinstance(node,ast.ImportFrom): names=[node.module or ""]
                assert not any(n.split(".")[0] in forbidden or "infrastructure" in n or "application" in n for n in names), path

def test_runner_has_no_io_or_goal_specific_branching():
    source=(ROOT/"application/runner.py").read_text()
    assert "sqlite3" not in source and "subprocess" not in source
    assert "development" not in source and "documentation" not in source
    assert "UPDATE" not in source and "INSERT" not in source
    for node in ast.walk(ast.parse(source)):
        if isinstance(node,(ast.Assign,ast.AugAssign,ast.AnnAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            assert not any(isinstance(t,ast.Attribute) and t.attr in {"status","stage_index","iteration","claimed_by"} for t in targets)
