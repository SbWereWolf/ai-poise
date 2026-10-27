"""Read-only Task-use instrumentation, installed before importing Poise."""
import importlib.abc
import sys


def observe():
    counts = {"task_imports": [], "poise_constructions": 0, "current_task_calls": 0}

    class Imports(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "poise.runtime" or fullname.startswith("poise.modules.tasks"):
                counts["task_imports"].append(fullname)
            return None

    def calls(frame, event, arg):
        if event != "call":
            return
        module, name = frame.f_globals.get("__name__", ""), frame.f_code.co_name
        if module.startswith("poise.") and name == "current_task":
            counts["current_task_calls"] += 1
        if module == "poise.runtime" and name == "__init__":
            obj = frame.f_locals.get("self")
            if obj is not None and type(obj).__name__ == "Poise":
                counts["poise_constructions"] += 1

    sys.meta_path.insert(0, Imports())
    sys.setprofile(calls)
    return counts
