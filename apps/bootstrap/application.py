"""CLI composition for configured external maintenance and result handlers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from subprocess import CompletedProcess
import sys

from .contracts import OperationContext, ResultHandler
from .infrastructure import CommandConfiguration, CommandExecutor, load_handler


class ForwardArgument(argparse.Action):
    """Let argparse preserve the order of external option/value pairs."""

    def __call__(self, parser, namespace, values, option_string=None):
        if namespace.forwarded is None:
            namespace.forwarded = []
        namespace.forwarded.append(option_string)
        if self.nargs == 0:
            setattr(namespace, self.dest, True)
        else:
            namespace.forwarded.append(values)


def _parse(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="bootstrap")
    parser.add_argument("command", choices=("infra", "deps", "check", "run"))
    parser.add_argument("--config", required=True, type=Path)
    context = parser.add_mutually_exclusive_group(required=True)
    context.add_argument("--catalog-dir")
    context.add_argument("--base-dir")
    parser.add_argument("--values", action=ForwardArgument)
    parser.add_argument("--set", action=ForwardArgument)
    parser.add_argument("--dry-run", action=ForwardArgument, nargs=0, default=False)
    parser.set_defaults(forwarded=None)
    parsed = parser.parse_args(arguments)
    if parsed.catalog_dir == "":
        parser.error("--catalog-dir must name a directory")
    if parsed.base_dir == "":
        parser.error("--base-dir must name a directory")
    if parsed.base_dir is not None:
        parsed.base_dir = Path(parsed.base_dir)
    return parsed


def _error(error: Exception, initial: CompletedProcess[bytes] | None) -> int:
    observations = []
    if initial is not None:
        observations.append({
            "stage": "initial", "stdout": repr(initial.stdout),
            "stderr": repr(initial.stderr), "exit_code": initial.returncode,
        })
    packet = {"status": "error", "exit_code": 4,
              "reason": f"{type(error).__name__}: {error}",
              "observations": observations, "actions": []}
    print(json.dumps(packet, ensure_ascii=False))
    return 4


def main(arguments: list[str] | None = None) -> int:
    parsed = _parse(list(sys.argv[1:] if arguments is None else arguments))
    initial = None
    try:
        configuration = CommandConfiguration.load(parsed.config)
        handler: ResultHandler = load_handler(configuration.handler_selector)
        selected = parsed.base_dir.absolute() if parsed.base_dir is not None else Path.cwd()
        values = {"base_dir": str(selected)} if parsed.base_dir is not None else {}
        executor = CommandExecutor(
            configuration.rendered_commands(parsed.catalog_dir, values=values),
            parsed.forwarded or [], selected,
        )
        initial = executor.run(parsed.command)
        context: OperationContext = {
            "operation": parsed.command, "dry_run": parsed.dry_run,
        }
        result = handler(initial, executor.run, configuration.handler_options, context)
        if not isinstance(result, dict) or type(result.get("exit_code")) is not int:
            raise ValueError("Обработчик должен вернуть JSON-объект с целым exit_code")
        encoded = json.dumps(result, ensure_ascii=False)
    except Exception as error:
        # Unknown plugin/transport failures go to the agent, never to another repair.
        return _error(error, initial)
    print(encoded)
    return result["exit_code"]
