"""Invoke one real transfer in a disposable coordinator; no caller override."""
import json
import sys

from conftest import WorkPoise
from poise.application.work import WorkTools


runtime = WorkPoise(sys.argv[1], 'receiver')
print(json.dumps(WorkTools(runtime).invoke(json.load(sys.stdin))), flush=True)
