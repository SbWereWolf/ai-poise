"""Pure policies for the closed Requirements entity/relation batch contract."""
from ..foundation.errors import DomainError
from .domain import _requirement


ENTITIES = {
    "requirements": ("id", "level", "status", "text"),
    "domain_areas": ("id", "business_name", "definition"),
    "domain_entities": ("id", "domain_id", "business_name", "code_name", "definition"),
    "domain_actions": ("id", "domain_id", "business_name", "code_name", "definition"),
}
RELATIONS = {
    "requirement_links": (("system_id", "application_id"),
                          {"system": "requirements", "application": "requirements"}),
    "requirement_entities": (("requirement_id", "domain_id", "entity_id"),
                             {"requirement": "requirements", "entity": "domain_entities"}),
    "requirement_actions": (("requirement_id", "domain_id", "action_id"),
                            {"requirement": "requirements", "action": "domain_actions"}),
}
REASONS = {
    "invalid_packet": "Invalid Requirements CRUD packet.",
    "invalid_item": "Invalid Requirements CRUD item.",
    "invalid_filter": "Invalid Requirements CRUD filter.",
    "invalid_cursor": "Invalid Requirements CRUD cursor.",
    "stale_cursor": "Requirements cursor revision changed.",
    "operand_cardinality": "Requirements relation operand A must select exactly one row.",
    "immutable_field": "Requirements CRUD identity or level is immutable.",
    "integrity_conflict": "Requirements CRUD integrity constraint failed.",
    "revision_conflict": "Requirements revision changed.",
    "request_conflict": "Requirements request_id was used for another request.",
    "store_error": "Requirements CRUD storage operation failed.",
}
# The current schema2 row policy, independent of the frozen historical DDL builder.
WHITESPACE = "".join(chr(n) for n in (
    9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760,
    8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200,
    8201, 8202, 8232, 8233, 8239, 8287, 12288,
))


class RequirementsCrudError(DomainError):
    def __init__(self, code, context=(None, None)):
        super().__init__(REASONS[code])
        self.code = code
        self.context = context

    def response(self):
        return {"status": "rejected", "reason": str(self), "error": {
            "code": self.code, "item_index": self.context[0], "item_id": self.context[1],
        }}


def require(condition, code, context=(None, None)):
    if not condition:
        raise RequirementsCrudError(code, context)


def scalar(value):
    return isinstance(value, str) and not any(0xD800 <= ord(c) <= 0xDFFF for c in value)


def scalar_tree(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if not scalar(item):
                return False
        elif isinstance(item, dict):
            if not all(scalar(key) for key in item):
                return False
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return True


def _copy_json(value):
    """Copy plain acyclic JSON containers without consuming the Python call stack."""
    if not isinstance(value, (dict, list)):
        return value
    result = {} if isinstance(value, dict) else []
    pending = [(value, result)]
    while pending:
        source, target = pending.pop()
        items = source.items() if isinstance(source, dict) else enumerate(source)
        for key, item in items:
            copied = {} if isinstance(item, dict) else [] if isinstance(item, list) else item
            if isinstance(target, dict):
                target[key] = copied
            else:
                target.append(copied)
            if isinstance(item, (dict, list)):
                pending.append((item, copied))
    return result


def is_crud_query_batch(queries):
    return isinstance(queries, list) and any(
        isinstance(q, dict) and q.get("kind") in ("entities", "relations") for q in queries
    )


def _items(items, maximum):
    require(isinstance(items, list) and 0 < len(items) <= maximum, "invalid_packet")
    ids = []
    for item in items:
        require(isinstance(item, dict), "invalid_packet")
        identifier = item.get("id")
        require(scalar(identifier) and bool(identifier), "invalid_packet")
        ids.append(identifier)
    require(len(ids) == len(set(ids)), "invalid_packet")


def validate_filter(table, value, maximum, context):
    require(isinstance(value, dict), "invalid_filter", context)
    for column, condition in value.items():
        require(scalar(column) and column in ENTITIES[table], "invalid_filter", context)
        require(isinstance(condition, dict) and len(condition) == 1, "invalid_filter", context)
        operator, operand = next(iter(condition.items()))
        if operator == "eq":
            require(scalar(operand), "invalid_filter", context)
        elif operator == "in":
            require(isinstance(operand, list) and len(operand) <= maximum,
                    "invalid_filter", context)
            require(all(scalar(v) for v in operand), "invalid_filter", context)
            require(len(operand) == len(set(operand)), "invalid_filter", context)
        else:
            raise RequirementsCrudError("invalid_filter", context)


def validate_row(table, row, context, previous=None):
    require(isinstance(row, dict) and set(row) == set(ENTITIES[table]), "invalid_item", context)
    require(all(scalar(v) for v in row.values()), "invalid_item", context)
    if table == "requirements":
        try:
            _requirement(row)
        except DomainError as exc:
            raise RequirementsCrudError("invalid_item", context) from exc
        if previous is not None:
            require(row["level"] == previous["level"], "immutable_field", context)
    else:
        for name, value in row.items():
            trimmed = value.strip(WHITESPACE)
            require(bool(trimmed) and (name == "definition" or value == trimmed),
                    "invalid_item", context)


def _operands(item, maximum, context):
    roles = RELATIONS[item["table"]][1]
    endpoints = []
    for name in ("a", "b"):
        operand = item[name]
        keys = {"endpoint", "filter"}
        require(isinstance(operand, dict) and (
            set(operand) == keys or name == "b" and set(operand) == {"endpoint"}
        ), "invalid_item", context)
        endpoint = operand.get("endpoint")
        require(scalar(endpoint) and endpoint in roles, "invalid_item", context)
        endpoints.append(endpoint)
        if "filter" in operand:
            validate_filter(roles[endpoint], operand["filter"], maximum, context)
    require(endpoints[0] != endpoints[1], "invalid_item", context)


def _item(item, maximum, context, query):
    kind, table = item.get("kind"), item.get("table")
    require(scalar(kind), "invalid_item", context)
    shapes = {
        "entities": {"id", "kind", "table", "filter", "page"},
        "relations": {"id", "kind", "table", "a", "b", "page"},
        "create": {"id", "kind", "table", "row"},
        "update": {"id", "kind", "table", "filter", "changes"},
        "delete": {"id", "kind", "table", "filter"},
        "link": {"id", "kind", "table", "a", "b"},
        "unlink": {"id", "kind", "table", "a", "b"},
    }
    if query and kind not in ("entities", "relations"):
        specialized = {
            "coverage": ({"id", "kind"}, {"id", "kind", "mode"}),
            "registry": ({"id", "kind"},), "gaps": ({"id", "kind"},),
            "chain": ({"id", "kind", "requirement_id"},),
            "plan_task": ({"id", "kind", "task_requirements"},),
        }
        require(kind in specialized and set(item) in specialized[kind] and scalar_tree(item),
                "invalid_item", context)
        return
    require(kind in (("entities", "relations") if query else
                     ("create", "update", "delete", "link", "unlink")), "invalid_item", context)
    require(set(item) == shapes[kind], "invalid_item", context)
    tables = RELATIONS if kind in ("relations", "link", "unlink") else ENTITIES
    require(scalar(table) and table in tables, "invalid_item", context)
    if "filter" in item:
        validate_filter(table, item["filter"], maximum, context)
    if kind == "create":
        validate_row(table, item["row"], context)
    if kind == "update":
        changes = item["changes"]
        require(isinstance(changes, dict) and bool(changes), "invalid_item", context)
        require(all(scalar(k) and k in ENTITIES[table] for k in changes), "invalid_item", context)
        require(all(scalar(v) for v in changes.values()), "invalid_item", context)
        require("id" not in changes, "immutable_field", context)
    if kind in ("relations", "link", "unlink"):
        _operands(item, maximum, context)
    if query:
        page = item["page"]
        require(isinstance(page, dict) and set(page) == {"limit", "after"}, "invalid_item", context)
        require(type(page["limit"]) is int and 1 <= page["limit"] <= maximum,
                "invalid_item", context)


def validate_query_batch(queries, maximum):
    _items(queries, maximum)
    for index, item in enumerate(queries):
        _item(item, maximum, (index, item["id"]), True)
    return _copy_json(queries)


def validate_command(request, maximum):
    require(isinstance(request, dict) and set(request) == {
        "request_id", "expected_revision", "operations",
    }, "invalid_packet")
    require(scalar(request["request_id"]) and bool(request["request_id"]), "invalid_packet")
    require(type(request["expected_revision"]) is int and request["expected_revision"] >= 0,
            "invalid_packet")
    _items(request["operations"], maximum)
    for index, item in enumerate(request["operations"]):
        _item(item, maximum, (index, item["id"]), False)
    return _copy_json(request)


def _normalized_filter(value):
    return {k: {op: sorted(v) if op == "in" else v for op, v in condition.items()}
            for k, condition in value.items()}


def normalize_selection(item):
    selection = {"kind": item["kind"], "table": item["table"]}
    if item["kind"] == "entities":
        selection["filter"] = _normalized_filter(item["filter"])
    else:
        for name in ("a", "b"):
            operand = item[name]
            selection[name] = {"endpoint": operand["endpoint"],
                               "filter": _normalized_filter(operand.get("filter", {}))}
    return selection


def validate_cursor(item, revision, maximum, context):
    cursor = item["page"]["after"]
    if cursor is None:
        return None
    require(isinstance(cursor, dict) and set(cursor) == {"revision", "selection", "key"}
            and scalar_tree(cursor), "invalid_cursor", context)
    require(type(cursor["revision"]) is int and cursor["revision"] >= 0, "invalid_cursor", context)
    require(cursor["revision"] == revision, "stale_cursor", context)
    selection = cursor["selection"]
    require(isinstance(selection, dict), "invalid_cursor", context)
    # Validate saved selection through the same policy, with cursor-owned failures.
    try:
        require(selection.get("kind") in ("entities", "relations"), "invalid_cursor", context)
        fields = ({"kind", "table", "filter"} if selection["kind"] == "entities"
                  else {"kind", "table", "a", "b"})
        require(set(selection) == fields, "invalid_cursor", context)
        saved = {**selection, "id": "cursor", "page": {"limit": 1, "after": None}}
        _item(saved, maximum, context, True)
        normalized = normalize_selection(saved)
    except RequirementsCrudError as exc:
        raise RequirementsCrudError("invalid_cursor", context) from exc
    require(normalized == normalize_selection(item), "invalid_cursor", context)
    arity = 1 if item["kind"] == "entities" else len(RELATIONS[item["table"]][0])
    key = cursor["key"]
    require(isinstance(key, list) and len(key) == arity and all(scalar(v) for v in key),
            "invalid_cursor", context)
    return key
