"""Test-owned selected Poise entry: observe source and project arguments."""
import json
import sys
from pathlib import Path

print(json.dumps({'source': str(Path(__file__).resolve()), 'arguments': sys.argv[1:]}))
