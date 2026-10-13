#!/usr/bin/env python3
"""An independent public application; no UAT implementation imports."""
import json
import runpy
import sys

subject = runpy.run_path(sys.argv[1])
print(json.dumps({"schema": "toy-public-result/v1", "value": subject["VALUE"]}, separators=(",", ":")))
