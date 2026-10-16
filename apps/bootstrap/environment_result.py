"""Configured result interpretation and one repair for the external v2 protocol."""

from collections.abc import Mapping
import json
from subprocess import CompletedProcess

from .contracts import InvokeCommand, OperationContext


def observation(stage: str, result: CompletedProcess[bytes]) -> dict[str, object]:
    def text(value: bytes) -> str:
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            # Preserve an unambiguous complete diagnostic for an invalid UTF-8 reply.
            return repr(value)
    return {"stage": stage, "stdout": text(result.stdout),
            "stderr": text(result.stderr), "exit_code": result.returncode}


def classify(result: CompletedProcess[bytes], options: Mapping[str, object]) -> str | None:
    try:
        packet = json.loads(result.stdout.decode("utf-8"))
        result.stderr.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(packet, dict) or packet.get("schema") != options["schema"]:
        return None
    status = packet.get("status")
    codes = options["status_exit_codes"]
    if not isinstance(status, str) or status not in codes or result.returncode != codes[status]:
        return None
    return status


def response(ready: bool, observations: list[dict], actions: list[str],
             options: Mapping[str, object], reason: str) -> dict[str, object]:
    value = {"status": "ready" if ready else "error",
             "exit_code": 0 if ready else options["failure_exit_code"],
             "observations": observations, "actions": actions}
    if not ready:
        value["reason"] = reason
    return value


def handle(initial: CompletedProcess[bytes], invoke: InvokeCommand,
           options: Mapping[str, object], context: OperationContext) -> dict[str, object]:
    observations = [observation("initial", initial)]
    actions = []
    failure_code = options["failure_exit_code"]
    if type(failure_code) is not int or failure_code == 0:
        raise ValueError("failure_exit_code должен быть ненулевым целым")
    status = classify(initial, options)
    if status is not None and status in options["success_statuses"]:
        return response(True, observations, actions, options, "")
    if status is None:
        return response(False, observations, actions, options,
                        "Неизвестный результат: проверьте полные потоки, схему и код завершения")
    if (status not in options["repairable_statuses"]
            or context["operation"] not in options["repair_operations"] or context["dry_run"]):
        return response(False, observations, actions, options,
                        "Окружение не готово; применимое разрешённое исправление отсутствует")

    def attempt(command: str, stage: str) -> str | None:
        actions.append(command)
        try:
            result = invoke(command)
        except (OSError, ValueError, KeyError) as error:
            observations.append({"stage": stage, "stdout": "", "stderr": str(error), "exit_code": None})
            return None
        observations.append(observation(stage, result))
        return classify(result, options)

    # Linear flow: a new result can never re-enter the correction decision.
    verification_command = options["verification_command"]
    repaired = attempt(options["repair_command"], "repair")
    verified = attempt(verification_command, "verification")
    ready = (repaired is not None and repaired in options["success_statuses"]
             and verified is not None and verified in options["success_statuses"])
    return response(ready, observations, actions, options,
                    "Единственная попытка исправления или последующая проверка завершилась ошибкой")
