"""Transparent test-owned public command; record calls and return observed input/cwd."""
import json
import os
from pathlib import Path
import sys

packet = json.load(sys.stdin)
with Path(sys.argv[1]).open("a", encoding="utf-8") as counter:
    counter.write("call\n")
print(json.dumps({"status": "ok", "tag": sys.argv[2], "packet": packet, "cwd": os.getcwd()}))
