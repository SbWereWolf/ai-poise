"""Read-only byte inventory of the selected installed previous distribution."""
import hashlib
from importlib.metadata import distribution
import json


selected = distribution('environment-maintenance')
inventory = {}
for relative in selected.files:
    path = selected.locate_file(relative)
    if path.is_file():
        inventory[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps(inventory, sort_keys=True))
