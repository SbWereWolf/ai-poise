"""Configured SDK adapters for Poise resource results and plain process exits."""
import json


class MaintenanceResult:
    def interpret(self, raw, options):
        value = json.loads(raw["stdout"])
        success = (raw["exit_code"] in options["accept"]
                   and value["schema"] == options["schema"]
                   and value[options["field"]] in options["values"])
        return {"success": success, "value": value,
                "error": None if success else "Resource result does not meet configured readiness"}


class ExitResult:
    def interpret(self, raw, options):
        success = raw["exit_code"] in options["accept"]
        return {"success": success, "value": {"exit_code": raw["exit_code"]},
                "error": None if success else "Command returned an unaccepted exit code"}


class ProjectListResult:
    """Interpret the configured selected-instance launch, not all list calls."""

    def interpret(self, raw, options):
        try:
            value = json.loads(raw["stdout"])
        except (ValueError, UnicodeDecodeError) as error:
            return {"success": False, "value": None, "error": str(error)}
        projects = value.get(options["projects_field"]) if isinstance(value, dict) else None
        errors = value.get(options["errors_field"]) if isinstance(value, dict) else None
        success = (raw["exit_code"] in options["accept"]
                   and isinstance(projects, list) and isinstance(errors, list)
                   and len(projects) >= options["minimum_project_count"]
                   and not errors)
        return {"success": success, "value": value,
                "error": None if success else "Selected instance is not ready: inspect the complete launch result"}


maintenance_result = MaintenanceResult()
exit_result = ExitResult()
project_list_result = ProjectListResult()
