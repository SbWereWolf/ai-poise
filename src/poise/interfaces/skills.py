"""Read-only skill metadata commands; no session, Task DB or lifecycle calls."""
import json

from ..common import exact_keys, digest
from ..application.skill_catalog import project_policy, validate_tasks
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
        elif operation in ("policy", "validate"):
            fields = {"operation", "skill_ids", "areas"}
            if operation == "validate":
                fields.add("tasks")
            exact_keys(request, fields, f"skills {operation}")
            policy = project_policy(catalog, request["skill_ids"], request["areas"])
            if operation == "policy":
                result, code = {"status": "ok", "policy": policy}, 0
            else:
                results = validate_tasks(request["tasks"], policy)
                valid = all(item["valid"] for item in results)
                result, code = {"status": "valid" if valid else "invalid", "results": results}, 0 if valid else 1
        else:
            raise PoiseError("Unknown skills operation")
        if operation in ("list", "read"):
            result, code = {"status": "ok", "skills": skills}, 0
        result["catalog_digest"] = digest(catalog.as_dict())
    except (PoiseError, UnicodeError, RecursionError) as exc:
        result, code = {"status": "rejected", "reason": str(exc)}, 2
    output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return code
