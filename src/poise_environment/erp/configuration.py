"""Bind only the fields owned by the selected resource adapter."""
from dataclasses import dataclass
import json
from pathlib import Path

from ..shared.templates import render_data


@dataclass(frozen=True)
class Resource:
    kind: str
    options: dict
    raw: dict


def bind_paths(parameters: dict, path_roots: dict) -> dict:
    bound = dict(parameters)
    for name, root_name in path_roots.items():
        bound[name] = str(Path(parameters[root_name]) / parameters[name])
    return bound


def load_resource(path: str, identifier: str, parameters: dict,
                  fields: dict[str, tuple[str, ...]]) -> Resource:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    parameters = bind_paths(parameters, document["path_roots"])
    raw = document["resources"][identifier]
    kind = raw["kind"]
    if kind not in fields:
        raise ValueError(f"Unknown resource kind: {kind}")
    options = {name: raw[name] for name in fields[kind]}
    return Resource(kind, render_data(options, parameters), raw)
