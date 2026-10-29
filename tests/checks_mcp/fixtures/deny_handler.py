"""A real independent downstream Pre denial, not a check owner or oracle."""
from copy import deepcopy
import json
import sys

packet = json.load(sys.stdin)
response = deepcopy(packet['response'])
specific = response.setdefault('hookSpecificOutput', {'hookEventName': 'PreToolUse'})
specific.update(permissionDecision='deny', permissionDecisionReason='Independent fixture denies this tool.')
json.dump({'schema': 'poise-hook-handler-result-1', 'invocation_id': packet['invocation_id'],
           'response': response, 'control': {'action': 'stop', 'reason': 'F13_DOWNSTREAM_DENY'}}, sys.stdout)
sys.stdout.write('\n')
