"""Read-only metadata and project instructions; no Task lifecycle calls."""
import json

from ..common import exact_keys, digest
from ..application.skill_catalog import project_policy, validate_tasks
from ..infrastructure.goal_config import strict_json
from ..infrastructure.skill_catalog import load_skill_catalog
from ..modules.foundation.errors import PoiseError
from ..modules.skills.instructions import InstructionInputError
from ..modules.skills.instruction_delivery import SCHEMA as DELIVERY_SCHEMA
from ..modules.skills.instruction_delivery import PORTABLE_SCHEMA
from ..modules.skills.portable_instructions import execute as deliver_selected
from ..application.project_instructions import ProjectInstructions
from ..infrastructure.project_instructions import FileInstructionSource


def execute(catalog_path, root, stream, output):
    try:
        request = strict_json(stream.read().decode("utf-8"))
    except (PoiseError, UnicodeError, RecursionError):
        result = InstructionInputError('invalid_request', 'request', 'provide:request').response()
        output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + '\n')
        return 2
    if not isinstance(request, dict):
        result = InstructionInputError('invalid_request', 'request', 'provide:request').response()
        output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + '\n')
        return 2
    if request.get('operation') in ('deliver_selected', 'read_selected'):
        result = deliver_selected(request)
        output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + '\n')
        return (2 if result['status'] == 'rejected' else
                1 if result['status'] == 'needs_inputs' else 0)
    delivery = request.get('operation') == 'deliver'
    bounded = delivery and ('budget' in request or 'continuation' in request)
    for field, value in (('catalog', catalog_path), ('root', root)):
        if value is None:
            result = InstructionInputError('invalid_request', field, 'provide:' + field).response()
            if delivery:
                result['schema'] = PORTABLE_SCHEMA if bounded else DELIVERY_SCHEMA
            output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + '\n')
            return 2
    if request.get('operation') in ('prepare', 'deliver'):
        try:
            service = ProjectInstructions(FileInstructionSource(catalog_path, root))
            result = service.deliver(request) if delivery else service.prepare(request)
            code = (2 if result['status'] == 'rejected' else
                    1 if result['status'] == 'needs_inputs' else 0)
        except InstructionInputError as exc:
            result, code = exc.response(), 2
            if delivery:
                result['schema'] = PORTABLE_SCHEMA if bounded else DELIVERY_SCHEMA
        output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + '\n')
        return code
    try:
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
