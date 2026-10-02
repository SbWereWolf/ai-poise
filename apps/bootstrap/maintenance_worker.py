"""Private snapshot adapter; environment-maintenance remains the execution owner."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile


def reply(core=None, error=None):
    sys.stdout.buffer.write((json.dumps({"core": core, "error": error}, ensure_ascii=False,
                                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))


def main():
    # Preserve the caller's interpreter/site policy: never seek another installation.
    try:
        from importlib.metadata import version
        from environment_maintenance.catalogue import Catalogue, ContractError, load_catalogue, validate_action
        from environment_maintenance.parameters import expand, resolve
        from environment_maintenance.engine import execute
        if version("environment-maintenance") != "0.1.1":
            raise ImportError("unsupported maintenance version")
    except (ImportError, ModuleNotFoundError):
        reply(error="maintenance_unavailable")
        return 0
    except Exception:
        reply(error="maintenance_unavailable")
        return 0

    started = False
    try:
        payload = json.load(sys.stdin)
        snapshot = payload["snapshot"]
        with tempfile.TemporaryDirectory(prefix="bootstrap-maintenance-") as directory:
            private = Path(directory)
            catalogue_file = private / "requirements.json"
            catalogue_file.write_text(json.dumps(snapshot["catalog"], ensure_ascii=False,
                                                allow_nan=False), encoding="utf-8")
            try:
                validated = load_catalogue(catalogue_file)
            except (ContractError, TypeError, KeyError):
                reply(error="invalid_target_catalogue")
                return 0
            catalogue = Catalogue(Path(payload["catalogue_path"]), validated.data)
            values = dict(snapshot["project"]["values"])
            for value_file in snapshot["values_files"]:
                values.update(value_file)
            try:
                params, _ = resolve(catalogue.data["parameters"], values,
                                    payload["environment"], payload["overrides"])
            except (ContractError, TypeError, KeyError):
                reply(error="invalid_target_parameters")
                return 0

            repairs_file = None
            if (snapshot["repairs"] is not None
                    and snapshot["repairs"].get("schema") == "stride-bootstrap-owner-commands/v1"):
                from owner_commands import project_owner_commands

                origin = Path(payload["repairs_path"]).parent

                def bind_directory(action, index):
                    # Only cwd crosses the snapshot relocation boundary. Keep argv,
                    # env and recommendations raw for the existing core expansion.
                    bound = copy.deepcopy(action)
                    cwd = Path(expand(action["cwd"], params)) if "cwd" in action else origin
                    if not cwd.is_absolute():
                        cwd = origin / cwd
                    alias = private / ("owner-cwd-" + str(index))
                    if "${" in str(alias):
                        raise ContractError("private path cannot preserve one-pass expansion")
                    alias.symlink_to(cwd, target_is_directory=True)
                    bound["cwd"] = str(alias)
                    return bound

                try:
                    projected = project_owner_commands(catalogue.data, snapshot["repairs"], params, bind_directory)
                    catalogue_file.write_text(json.dumps(projected, ensure_ascii=False,
                                                        allow_nan=False), encoding="utf-8")
                    projected_catalogue = load_catalogue(catalogue_file)
                    catalogue = Catalogue(Path(payload["catalogue_path"]), projected_catalogue.data)
                except (ContractError, TypeError, KeyError, ValueError):
                    reply(error="invalid_target_catalogue")
                    return 0
            elif snapshot["repairs"] is not None:
                # RepairRouter owns action/route validation and expansion. Only cwd
                # needs an adapter: its source directory is not our private directory.
                repairs = copy.deepcopy(snapshot["repairs"])
                origin = Path(payload["repairs_path"]).parent
                try:
                    for index, (key, action) in enumerate(repairs["repairs"].items()):
                        validate_action(action, key)
                        if "repair" in action:
                            raise ContractError("recursive route")
                        cwd = Path(expand(action["cwd"], params)) if "cwd" in action else origin
                        if not cwd.is_absolute():
                            cwd = origin / cwd
                        alias = private / ("cwd-" + str(index))
                        # The opaque alias has no placeholders. Raw argv/env are left
                        # untouched; even placeholder-looking substituted cwd values
                        # and literal source-directory names are expanded only once.
                        if "${" in str(alias):
                            raise ContractError("private path cannot preserve one-pass expansion")
                        alias.symlink_to(cwd, target_is_directory=True)
                        action["cwd"] = str(alias)
                except (ContractError, TypeError, KeyError, ValueError):
                    reply(error="invalid_target_catalogue")
                    return 0
                repairs_file = private / "repairs.json"
                repairs_file.write_text(json.dumps(repairs, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            try:
                # The owner validates every expanded action and route before running
                # any command. Do not copy its check/apply/recheck or redaction logic.
                started = True
                core = execute(catalogue, payload["command"], values=values,
                               overrides=payload["overrides"], environ=payload["environment"],
                               dry_run=payload["dry_run"], repair_catalog=repairs_file)
            except ContractError:
                reply(error="invalid_target_catalogue")
                return 0
            reply(core=core)
    except Exception:
        # Never expose parameters, private paths, exception messages or tracebacks.
        reply(error="worker_failed" if started else "target_input_unavailable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
