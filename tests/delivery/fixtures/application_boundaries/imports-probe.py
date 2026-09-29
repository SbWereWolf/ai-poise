"""Test-owned fresh-process observations; the candidate is never the oracle."""
import importlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
sys.path[:0] = [str(root), str(root / "src")]
mode = sys.argv[2]
fixture = Path(sys.argv[3])
if mode == "requirements":
    importlib.import_module("apps.requirements.shell")
    observed = [n for n in ("poise.infrastructure.goal_config", "poise.runtime") if n in sys.modules]
    result = {"unexpected": observed}
elif mode == "bare":
    import poise
    from poise import PoiseError
    result = {"unexpected": [n for n in ("poise.runtime",) if n in sys.modules]}
elif mode == "api":
    import poise
    from poise import Poise, PoiseError
    from poise.runtime import Poise as RuntimePoise
    from poise.modules.foundation.errors import PoiseError as FoundationError
    scope = {}
    exec("from poise import *", scope)
    assert Poise is RuntimePoise is poise.Poise is scope["Poise"]
    assert PoiseError is FoundationError is scope["PoiseError"]
    assert poise.__all__ == ["Poise", "PoiseError"]
    assert poise.__dict__["Poise"] is Poise
    try:
        getattr(poise, "unknown_public_entity")
    except AttributeError:
        pass
    else:
        raise AssertionError("unknown attribute did not raise AttributeError")
    result = {"passed": True}
elif mode == "legacy":
    from poise.infrastructure.goal_config import strict_json
    from poise import PoiseError
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    for case in cases["successes"]:
        assert strict_json(case["input"]) == case["expected"], case
    for case in cases["errors"]:
        try:
            strict_json(case["input"])
        except PoiseError as error:
            assert str(error) == case["error"], (case, str(error))
        else:
            raise AssertionError("invalid parser input was accepted")
    result = {"passed": True}
elif mode == "helper":
    from poise.modules.foundation.strict_json import strict_json as new
    from poise.infrastructure.goal_config import strict_json as old
    assert new is old
    result = {"passed": True}
else:
    raise AssertionError("unknown test probe")
sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
