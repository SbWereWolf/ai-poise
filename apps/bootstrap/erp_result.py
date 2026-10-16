"""ERP configuration facts supplement, but never replace, command outcomes."""
from collections.abc import Mapping
import hashlib
from pathlib import Path
from subprocess import CompletedProcess

from .contracts import InvokeCommand, OperationContext
from .environment_result import handle as handle_environment


def selection(options: Mapping[str, object]) -> list[dict]:
    targets = options["configuration_targets"]
    if not isinstance(targets, list) or not targets:
        raise ValueError(
            "configuration_targets must be a nonempty list")
    identifiers = set()
    for target in targets:
        if not isinstance(target, dict) or any(
                not isinstance(target.get(key), str) or not target[
                    key].strip()
                for key in ("id", "kind", "path")
        ):
            raise ValueError(
                "Each configuration target requires id, kind and path")
        if target["id"] in identifiers:
            raise ValueError(
                "Configuration target IDs must be unique")
        identifiers.add(target["id"])
    return targets


def observe_configuration(target: dict) -> dict[str, object]:
    record = {"id": target["id"], "kind": target["kind"],
              "state": "unavailable", "sha256": None, "cause": ""}
    try:
        content = Path(target["path"]).read_bytes()
    except FileNotFoundError:
        record.update(state="absent", cause="not-found")
    except PermissionError:
        record["cause"] = "permission-denied"
    except OSError as error:
        record["cause"] = f"filesystem-error-{error.errno}"
    else:
        record.update(state="present",
                      sha256=hashlib.sha256(content).hexdigest())
    return record


def handle(initial: CompletedProcess[bytes], invoke: InvokeCommand,
           options: Mapping[str, object],
           context: OperationContext) -> dict[str, object]:
    targets = selection(options)
    result = handle_environment(initial, invoke, options, context)
    observed = [observe_configuration(target) for target in targets]
    result["configuration_observations"] = observed
    if result["status"] == "ready" and any(
            record["state"] != "present" for record in observed
    ):
        result.update(status="error",
                      exit_code=options["failure_exit_code"],
                      reason="Required configuration could not be observed")
    return result
