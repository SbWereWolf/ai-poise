"""Observe a configured rollout against the existing project inventory.

Completeness is a pure policy. The use case composes observations through the
existing process owner; this command never installs packages or edits consumers.
"""
import argparse
import json
from pathlib import Path
import tempfile

from poise.execution import run_command


def inventory_errors(inventory, consumers, exclusions):
    """Every registered project has exactly one explicit classification."""
    errors = []
    if inventory.get("status") != "listed" or inventory.get("errors"):
        errors.append("Project inventory contains errors")
    registered = [row["project"] for row in inventory["projects"]]
    serviced = [row["project"] for row in consumers]
    excluded = [row["project"] for row in exclusions if row["reason"].strip()]
    classified = serviced + excluded
    if len(set(registered)) != len(registered):
        errors.append("Project inventory contains duplicate identities")
    if len(set(classified)) != len(classified):
        errors.append("Consumer classifications overlap")
    if set(registered) != set(classified):
        errors.append("Consumer classifications do not cover the project inventory")
    return errors


class CommandObservations:
    """Adapt explicit commands to the canonical complete-capture runner."""

    def __init__(self, directory, timeout):
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="update-", dir=root))
        self.timeout = timeout
        self.sequence = 0

    def execute(self, command):
        self.sequence += 1
        directory = self.directory / str(self.sequence)
        directory.mkdir()
        stdout, stderr = directory / "stdout", directory / "stderr"
        result = run_command(command["argv"], Path(command["cwd"]), command["env"],
                             self.timeout, stdout, stderr)
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        if result["timed_out"] or not result["capture_complete"]:
            raise ValueError("Command did not complete; inspect " + str(directory))
        return result["actual_exit_code"], stdout.read_text(encoding="utf-8")


def observe_consumer(consumer, configuration, execute):
    """Observe one selected entry, its optional SDK and actual compatibility."""
    errors = []
    row = {"project": consumer["project"], "entry_version": None,
           "sdk_version": None, "compatibility_status": None, "ready": False}
    compatibility = consumer["compatibility"]
    try:
        preparation = json.loads(Path(consumer["preparation_config"]).read_text())
        code, stdout = execute({"argv": [preparation["parameters"]["start_app"], "--version"],
                                "cwd": compatibility["cwd"], "env": compatibility["env"]})
        row["entry_version"] = stdout.rstrip("\n")
        if code != 0 or stdout != configuration["expected_entry_stdout"]:
            errors.append("Selected entry does not match the configured supply")
        runtime = consumer["runtime"]
        if runtime is not None:
            # runtime.env is trusted executable input, sourced by its Bash owner.
            probe = ('import importlib.metadata; '
                     'print(importlib.metadata.version("environment-maintenance"))')
            script = 'set -euo pipefail\nsource "$1"\nexec "${!2}" -I -B -c "$3"'
            code, stdout = execute({"argv": ["/bin/bash", "-c", script, "sdk-observation",
                runtime["path"], runtime["python_key"], probe],
                "cwd": compatibility["cwd"], "env": compatibility["env"]})
            row["sdk_version"] = stdout.strip()
            if code != 0 or row["sdk_version"] != configuration["expected_sdk_version"]:
                errors.append("Selected SDK does not match the configured supply")
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(str(error))
    # Compatibility is an independent observation, including when selection fails.
    try:
        code, stdout = execute(compatibility)
        row["compatibility_status"] = json.loads(stdout)["status"]
        if (code != compatibility["expected_exit_code"]
                or row["compatibility_status"] != compatibility["expected_status"]):
            errors.append("Configured compatibility check failed")
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(str(error))
    row["ready"] = not errors
    row["errors"] = errors
    return row


def observe_update(configuration, execute):
    """Verify a rollout without maintaining another application registry."""
    errors = []
    try:
        code, stdout = execute(configuration["inventory"])
        inventory = json.loads(stdout)
        errors.extend(inventory_errors(inventory, configuration["consumers"],
                                       configuration["excluded_projects"]))
        if code != 0:
            errors.append("Project inventory command failed")
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(str(error))
    consumers = [observe_consumer(consumer, configuration, execute)
                 for consumer in configuration["consumers"]]
    errors.extend(row["project"] + ": " + error
                  for row in consumers for error in row["errors"])
    return {"status": "error" if errors else "ready", "errors": errors,
            "consumers": consumers}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Проверить обновление всех потребителей")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        configuration = json.loads(Path(args.config).read_text())
        observations = CommandObservations(configuration["capture_directory"],
                                            configuration["timeout_seconds"])
        result = observe_update(configuration, observations.execute)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {"status": "error", "errors": [str(error)], "consumers": []}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
