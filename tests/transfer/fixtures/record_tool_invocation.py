"""Observe actual tool entry, then replace this process with the configured tool."""
import json
import os
from pathlib import Path
import sys

log, executable, *arguments = sys.argv[1:]
with Path(log).open('a') as output:
    output.write(json.dumps([executable, *arguments]) + '\n')
os.execv(executable, [executable, *arguments])
