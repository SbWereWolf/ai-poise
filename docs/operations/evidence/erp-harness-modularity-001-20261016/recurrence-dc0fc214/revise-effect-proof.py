"""Generate a distinct reviewed proposal; preserve the first proposal bytes."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent
original = root / "operator-recovery.py"
assert hashlib.sha256(original.read_bytes()).hexdigest() == "ab8ac1e06c46911f5aed3a832ffb5d95446609a7233170a86e7774136a84132f"
source = original.read_text()
replacements = {
    'PLAN = ARCHIVE / "operator-plan.json"': 'PLAN = ARCHIVE / "operator-plan-v2.json"',
    'APPLIED = ARCHIVE / "operator-applied.json"': 'APPLIED = ARCHIVE / "operator-applied-v2.json"',
    'ambiguous = b"registered-command-adoption.py" in command': 'ambiguous = any(marker in command for marker in (b"harness-runner-contracts", b"ai-assistant/tests/checks/run-check.py", b"ai-assistant/tests/checks/fixtures/command_fixture.py"))',
    '"adoption_command": ambiguous': '"runner_contract_command": ambiguous',
    'Present Task/ambiguous adoption writers': 'Present Task/ambiguous runner-contract writers',
    'same-uid Task checkout cwd/arguments and adoption command; current invocation ancestors excluded': 'same-uid Task checkout cwd/arguments, runner-contract profile and exact suite/fixture argv markers; current invocation ancestors excluded',
    'Unit registry, telemetry and fixture files are created below configured ROOT/.runtime/tmp/python/registered-command-adoption-*; helper subprocesses use cwd=ROOT. Original UNKNOWN is not inferred from current absence.': 'Actual method invokes run_check.py --command-id harness-runner-contracts in ROOT. Its configured profile invokes tests/checks/run-check.py. python-runtime.sh sets TMPDIR=ROOT/.runtime/tmp/python; temp_root creates tempfile roots below it, with local .runtime evidence/cache and ai-assistant/checks/data. Fixture commands use cwd=temp_root; spawned and detached python -c children inherit that cwd, including output holders, so ROOT-descendant cwd inspection covers them even without a Task path in argv. Outer runner evidence and adoption ownership remain below ROOT/.runtime. Current absence does not infer historical termination, capture or UNKNOWN outcome.',
}
for old, new in replacements.items():
    assert old in source, old
    source = source.replace(old, new)
target = root / "operator-recovery-v2.py"
with target.open("x", encoding="utf-8") as stream:
    stream.write(source)
print(json.dumps({"script": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}))
