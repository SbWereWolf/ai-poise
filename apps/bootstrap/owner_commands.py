"""Project public owner commands into ordinary maintenance actions, without I/O.

The existing environment-maintenance engine remains the only execution owner.
The worker supplies directory binding because it owns the private snapshot area.
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from environment_maintenance.catalogue import ContractError, validate_action
from environment_maintenance.parameters import expand

SCHEMA = "stride-bootstrap-owner-commands/v1"


def text(value: Any, *, nonblank: bool = False) -> bool:
    if not isinstance(value, str) or "\0" in value or (nonblank and not value.strip()):
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return True


def project_owner_commands(
    catalogue: dict[str, Any],
    document: dict[str, Any],
    parameters: dict[str, Any],
    bind_directory: Callable[[dict[str, Any], int], dict[str, Any]],
) -> dict[str, Any]:
    """Validate all bindings and routes; return a detached catalogue projection.

    Expansion here validates but never becomes the next expansion's input.
    Raw argv/env/recommendation pass to the core for their one execution expansion.
    Only cwd is replaced by the worker's opaque directory alias.
    """
    if (not isinstance(document, dict) or set(document) != {"schema", "repairs"}
            or document.get("schema") != SCHEMA or not isinstance(document["repairs"], dict)):
        raise ContractError("invalid owner command document")
    bindings = copy.deepcopy(document["repairs"])
    # Validate even unused entries and expanded values before creating projections.
    for key, entry in bindings.items():
        if not text(key) or not key:
            raise ContractError("invalid owner command identity")
        if not isinstance(entry, dict) or set(entry) != {"check", "apply", "recommendation"}:
            raise ContractError("invalid owner command binding fields")
        if not text(entry["recommendation"], nonblank=True):
            raise ContractError("owner recommendation must be nonempty text")
        if not text(expand(entry["recommendation"], parameters), nonblank=True):
            raise ContractError("invalid expanded owner recommendation")
        for mode in ("check", "apply"):
            action = entry[mode]
            validate_action(action, key)
            if "repair" in action:
                raise ContractError("recursive owner command route")
            validate_action(expand(action, parameters), key)

    result = copy.deepcopy(catalogue)
    # Complete route preflight also includes requirements of unselected kinds.
    for requirement in result["requirements"]:
        for mode in ("check", "apply", "recommendation"):
            action = requirement[mode]
            if isinstance(action, dict) and "repair" in action:
                if (set(action) != {"repair"} or action["repair"] != requirement["id"]
                        or action["repair"] not in bindings):
                    raise ContractError("missing or mismatched owner command route")
    for index, entry in enumerate(bindings.values()):
        for offset, mode in enumerate(("check", "apply")):
            entry[mode] = bind_directory(entry[mode], index * 2 + offset)
    for requirement in result["requirements"]:
        for mode in ("check", "apply", "recommendation"):
            action = requirement[mode]
            if isinstance(action, dict) and "repair" in action:
                requirement[mode] = copy.deepcopy(bindings[action["repair"]][mode])
    return result
