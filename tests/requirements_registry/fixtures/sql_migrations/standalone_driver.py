"""Test-owned stock-tool process: importing Poise is an explicit failure."""

import importlib.abc
import json
import sys


class NoPoise(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "poise" or fullname.startswith("poise."):
            raise RuntimeError("SQL migrations must not import Poise")
        return None


sys.meta_path.insert(0, NoPoise())
from yoyo import get_backend, read_migrations

directory, database, action = sys.argv[1:]
backend = get_backend("sqlite:///" + database)
migrations = read_migrations(*json.loads(directory))
with backend.lock():
    if action == "apply":
        backend.apply_migrations(backend.to_apply(migrations))
    elif action == "rollback":
        backend.rollback_migrations(backend.to_rollback(migrations))
    else:
        raise RuntimeError("Unsupported test action")
assert not any(name == "poise" or name.startswith("poise.") for name in sys.modules)
