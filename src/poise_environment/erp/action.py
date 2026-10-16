"""Dispatch one explicitly selected resource; the external tool owns ordering."""
import argparse

from ..shared.protocol import serve
from . import application, dependencies, environment, files, tools
from .configuration import load_resource

FIELDS = {
    "json": ("source", "target"),
    "directories": ("paths", "mode"),
    "tool": ("argv", "cwd", "bound_seconds", "expected"),
    "service_tools": ("commands",),
    "php_extension": ("argv", "cwd", "bound_seconds", "extension"),
    "environment": ("reader", "read_argv", "validation_argv",
                    "configure_argv", "settings", "cwd",
                    "bound_seconds"),
    "units": ("php", "helper", "cwd", "bound_seconds", "applications",
              "values", "canonical_keys"),
    "databases": ("read_argv", "cwd", "bound_seconds", "keys"),
    "queues": ("read_argv", "cwd", "bound_seconds", "keys",
               "command"),
    "composer": ("php", "helper", "projects", "environment",
                 "probe_bound_seconds", "install_bound_seconds"),
    "node": ("runtime", "cwd", "required_files", "install_argv", "environment",
             "bound_seconds"),
    "mirrors": ("cwd", "paths_argv", "repair_argv", "environment",
                "bound_seconds", "packages", "excluded_directories"),
    "playwright": ("required_files",),
    "artifact": ("path", "format", "validator", "root_tag",
                 "include_tag", "cwd", "bound_seconds", "repair_argv",
                 "environment", "repair_bound_seconds"),
    "discovery": ("command", "probe_argv", "path", "cwd",
                  "bound_seconds"),
    "migrations": ("read_argv", "cwd", "bound_seconds", "keys",
                   "tables", "command", "command_cwd"),
    "settings": ("read_argv", "cwd", "bound_seconds", "keys", "query",
                 "required_rows", "command", "command_cwd"),
    "readiness": ("databases", "settings", "cjm_argv", "cwd",
                  "bound_seconds"),
}


def observe(args):
    resource = load_resource(args.resource_file, args.requirement_id,
                             vars(args), FIELDS)
    if resource.kind == "databases":
        return files.application_databases(
            environment.selected_paths(resource.options), args.action)
    handlers = {
        "json": files.json_document, "directories": files.directories,
        "tool": tools.tool, "service_tools": tools.service_tools,
        "php_extension": tools.php_extension,
        "environment": environment.environment,
        "units": environment.installation_units,
        "queues": application.queue_schema,
        "composer": dependencies.composer_dependencies,
        "node": dependencies.node_dependencies,
        "mirrors": dependencies.mirrored_packages,
        "playwright": dependencies.playwright,
        "artifact": application.artifact,
        "discovery": application.discovery,
        "migrations": application.migrations,
        "settings": application.bootstrap_settings,
        "readiness": application.readiness,
    }
    return handlers[resource.kind](resource.options, args.action)


def main():
    parser = argparse.ArgumentParser(
        description="Обслужить выбранный ресурс ERP")
    for name in ("resource-file", "python", "repository", "profile-root",
                 "php-executable",
                 "node-executable", "composer-executable",
                 "npm-entry-point",
                 "environment-file", "local-settings-path",
                 "host-runtime-settings-path",
                 "cjm-settings-path", "local-settings-source",
                 "host-runtime-settings-source",
                 "cjm-settings-source"):
        parser.add_argument("--" + name, required=True)
    return serve(parser, observe)


if __name__ == "__main__":
    raise SystemExit(main())
