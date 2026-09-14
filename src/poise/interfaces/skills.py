"""Read-only skill metadata commands; no session, Task DB or lifecycle calls."""
import json

from ..common import exact_keys, digest
from ..infrastructure.goal_config import strict_json
from ..infrastructure.skill_catalog import load_skill_catalog
from ..modules.foundation.errors import PoiseError


def execute(catalog_path, root, stream, output):
    try:
        request = strict_json(stream.read().decode("utf-8"))
        if not isinstance(request, dict):
            raise PoiseError("skills request must be an object")
        catalog = load_skill_catalog(catalog_path, root)
        operation = request.get("operation")
        if operation == "list":
            exact_keys(request, {"operation"}, "skills list")
            skills = catalog.as_dict()["skills"]
        elif operation == "read":
            exact_keys(request, {"operation", "skill_ids"}, "skills read")
            skills = catalog.select(request["skill_ids"])
        else:
            raise PoiseError("Unknown skills operation")
        result = {"status": "ok", "catalog_digest": digest(catalog.as_dict()), "skills": skills}
        code = 0
    except (PoiseError, UnicodeError, RecursionError) as exc:
        result, code = {"status": "rejected", "reason": str(exc)}, 2
    output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return code
