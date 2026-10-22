"""Observe unchanged public calls while executing one actual example route."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(root / 'examples'))
from poise.application.work import WorkTools
from catalogue_walkthrough import run

original = WorkTools.invoke
calls = []


def observed(self, packet):
    result = original(self, packet)
    # Full independent witness only; no return/authorization/state replacement.
    calls.append({'packet': packet, 'response': result})
    return result


WorkTools.invoke = observed
directory, goal, scenario = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
report = run(directory, goal, scenario)
(directory / 'public-call-witness.json').write_text(
    json.dumps(calls, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
assert report['status'] == 'PASS'
