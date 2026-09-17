from __future__ import annotations

from ..modules.foundation.errors import DomainError


class RequirementsCommands:
    """Bounded declarative command and query API for one project registry."""

    def __init__(self, store, max_items):
        if type(max_items) is not int or max_items <= 0:
            raise DomainError("Requirements max_items must be a positive integer")
        self.store = store
        self.max_items = max_items

    def apply(self, request):
        return self.store.apply(request, self.max_items)

    def query(self, queries):
        if not isinstance(queries, list) or not queries or len(queries) > self.max_items:
            raise DomainError("Requirements queries must be bounded and nonempty")
        identifiers = [query.get("id") for query in queries if isinstance(query, dict)]
        if len(identifiers) != len(queries) or any(not isinstance(value, str) or not value for value in identifiers):
            raise DomainError("Every Requirements query requires an id")
        if len(identifiers) != len(set(identifiers)):
            raise DomainError("Requirements query ids must be unique")
        revision, registry = self.store.read()
        results = []
        for query in queries:
            kind = query.get("kind")
            if kind == "coverage":
                if set(query) not in ({"id", "kind"}, {"id", "kind", "mode"}):
                    raise DomainError("coverage query has unknown fields")
                value = registry.coverage(query.get("mode"))
            elif kind in ("gaps", "registry"):
                if set(query) != {"id", "kind"}:
                    raise DomainError(f"{kind} query has unknown fields")
                value = registry.gaps() if kind == "gaps" else registry.to_dict()
            elif kind == "plan_task":
                if set(query) != {"id", "kind", "task_requirements"}:
                    raise DomainError("plan_task query requires task_requirements")
                requirements = query["task_requirements"]
                if not isinstance(requirements, list) or len(requirements) > self.max_items:
                    raise DomainError("Task Requirements plan exceeds max_items")
                value = registry.plan_task(requirements)
            elif kind == "chain":
                if set(query) != {"id", "kind", "requirement_id"}:
                    raise DomainError("chain query requires requirement_id")
                value = registry.chain(query["requirement_id"])
            else:
                raise DomainError(f"unknown Requirements query: {kind}")
            results.append({"id": query["id"], "value": value})
        return {"revision": revision, "results": results}

    def import_bootstrap(self, path, request_id):
        document = self.store.read_bootstrap(path)
        if not isinstance(document, dict) or set(document) != {
            "schema",
            "purpose",
            "system_requirements",
            "application_requirements",
            "derivations",
        }:
            raise DomainError("Requirements bootstrap has an invalid document contract")
        if document["schema"] != "requirements-bootstrap-1":
            raise DomainError("Requirements bootstrap schema is unsupported")
        operations = []
        for item in document["system_requirements"]:
            if not isinstance(item, dict) or set(item) != {"id", "status", "text"}:
                raise DomainError("System bootstrap requirement has invalid fields")
            operations.append({
                "kind": "put_requirement",
                "requirement": {**item, "level": "system"},
            })
        for item in document["application_requirements"]:
            if not isinstance(item, dict) or set(item) != {"id", "application", "status", "text"}:
                raise DomainError("Application bootstrap requirement has invalid fields")
            operations.append({
                "kind": "put_requirement",
                "requirement": {
                    "id": item["id"],
                    "level": "application",
                    "status": item["status"],
                    "text": item["text"],
                },
            })
        for derivation in document["derivations"]:
            if not isinstance(derivation, dict) or set(derivation) != {"child", "parents"}:
                raise DomainError("Requirements bootstrap derivation has invalid fields")
            if not isinstance(derivation["parents"], list) or not derivation["parents"]:
                raise DomainError("Requirements bootstrap derivation requires parents")
            operations.extend(
                {
                    "kind": "link",
                    "system": parent,
                    "application": derivation["child"],
                }
                for parent in derivation["parents"]
            )
        return self.apply({
            "request_id": request_id,
            "expected_revision": 0,
            "operations": operations,
        })
