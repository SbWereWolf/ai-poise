"""Observe the existing snapshot owner under explicit compatible Git settings."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from poise.common import load_config
from poise.infrastructure.git_snapshot import snapshot_tree

ROOT = Path("/home/sbwerewolf/workdata/erp/worktrees/ERP-HARNESS-MODULARITY-001")
CONFIG = Path("/home/sbwerewolf/workdata/poise-op/projects/erp/config/project.json")
OUT = Path(__file__).resolve().parent
_, cfg, _ = load_config(CONFIG)
limit = cfg["limits"]["git_seconds"]
base_env = dict(os.environ)
count = base_env.get("GIT_CONFIG_COUNT", "0")
if not count.isdecimal():
    raise RuntimeError("Existing Git environment count is invalid")
slot = int(count)
base_env.update({"GIT_CONFIG_COUNT": str(slot + 1),
                 f"GIT_CONFIG_KEY_{slot}": "core.fsmonitor",
                 f"GIT_CONFIG_VALUE_{slot}": "false",
                 "GIT_TRACE2_EVENT": str(OUT / "trace.jsonl")})
report = {"kind": "snapshot-owner-diagnostic", "configured_git_seconds": limit,
          "explicit_process_only_git_config": {"core.fsmonitor": False},
          "shared_configuration_written": False, "task_operation_invoked": False,
          "native_acceptance": False, "commands": []}

def git(where, *args, env=None):
    actual_env = dict(base_env if env is None else env)
    started = time.monotonic()
    item = {"arguments": list(args), "timeout_seconds": limit}
    report["commands"].append(item)
    try:
        result = subprocess.run([cfg["git"]["executable"], "-C", str(where), *args],
                                env=actual_env, capture_output=True, text=True,
                                timeout=limit, check=True)
        item["exit_code"] = result.returncode
        return result.stdout.strip()
    except Exception as exc:
        item["error"] = str(exc)
        raise
    finally:
        item["duration_seconds"] = time.monotonic() - started

try:
    if git(ROOT, "config", "--get", "core.fsmonitor") != "false":
        raise RuntimeError("Explicit process-only Git configuration was not applied")
    index = Path(git(ROOT, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = ROOT / index
    report["real_index_sha256_before"] = hashlib.sha256(index.read_bytes()).hexdigest()
    expected = git(ROOT, "rev-parse", "HEAD^{tree}")
    # The canonical owner copies os.environ into its invocation environment.
    # This diagnostic process declares its Git configuration before observation.
    os.environ.update(base_env)
    observed = snapshot_tree(git, ROOT, OUT / "indexes", "owned-observation-")
    report.update(observed_tree=observed, expected_head_tree=expected,
                  real_index_sha256_after=hashlib.sha256(index.read_bytes()).hexdigest())
    report["matches_head"] = observed == expected
    report["real_index_unchanged"] = report["real_index_sha256_before"] == report["real_index_sha256_after"]
    report["status"] = "observed"
finally:
    (OUT / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("kind", "configured_git_seconds", "native_acceptance")}
                     | {"status": report.get("status", "failed"), "result": str(OUT / "result.json")}))
