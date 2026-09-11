"""Reproduce behavioral negative controls in disposable copies, never the source tree."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run(source: Path, output: Path):
    mutations=[
        ("inspect-always-clear", "src/poise/modules/workflow/handlers.py",
         '"changes_requested" if updated.open_findings else "clear"', '"clear"',
         "tests/runner/test_route_domain.py::test_full_feedback_rejection_then_acceptance_same_task"),
        ("next-by-list-index", "src/poise/modules/tasks/domain.py",
         'target = self.route.node(self.stage.stage_id).target(self.progress.outcome)',
         'target = self.stages[(self.state.stage_index + 1) % len(self.stages)].stage_id',
         "tests/runner/test_route_domain.py::test_graph_is_not_list_order"),
        ("no-visit-budget", "src/poise/modules/workflow/domain.py",
         'if visits[target] >= self.max_stage_visits:', 'if False:',
         "tests/runner/test_route_domain.py::test_limits_persist_and_do_not_block_cancellation"),
    ]
    results=[]
    for name,file,needle,replacement,test in mutations:
        with tempfile.TemporaryDirectory(prefix="poise-runner-mutation-") as folder:
            root=Path(folder)
            for top in ("src","tests"):
                shutil.copytree(source/top,root/top,ignore=shutil.ignore_patterns("__pycache__"))
            path=root/file;original=path.read_text()
            if original.count(needle)!=1:
                raise RuntimeError(f"Mutation target is not unique: {name}")
            path.write_text(original.replace(needle,replacement))
            result=subprocess.run([sys.executable,"-m","pytest","-q","--tb=short",test],cwd=root,
                env={**os.environ,"PYTHONPATH":str(root/"src")},capture_output=True,text=True,timeout=30)
            detected=result.returncode==1 and "failed" in result.stdout and "ERROR collecting" not in result.stdout
            results.append({"name":name,"test":test,"exit_code":result.returncode,"detected":detected,
                            "output":result.stdout+result.stderr})
    output.write_text(json.dumps(results,ensure_ascii=False,indent=2)+"\n")
    if not all(r["detected"] for r in results):
        raise SystemExit("A mutation escaped; inspect output")
    print(json.dumps([{k:r[k] for k in ("name","detected")} for r in results]))


if __name__=="__main__":
    if len(sys.argv)!=3:
        raise SystemExit("Usage: check_runner_mutations.py SOURCE_ROOT OUTPUT_JSON")
    run(Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve())
