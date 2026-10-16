"""Installed shared launcher composition; invoke through the versioned Bash entry."""
from __future__ import annotations

import argparse
from dataclasses import replace
import sys

from .application.project_launch import ProjectLaunchCommands
from .infrastructure.project_launch import FileProjectLaunch
from .modules.foundation.errors import PoiseError


def main() -> int:
    parser = argparse.ArgumentParser(description='Run an explicitly configured project operation.')
    parser.add_argument('--config', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    arguments = options.arguments
    if arguments and arguments[0] == '--':
        arguments = arguments[1:]
    port = FileProjectLaunch()
    settings = None
    try:
        settings = replace(port.read_settings(options.config), project_id=options.project)
        ProjectLaunchCommands(port).execute(settings, arguments)
    except (PoiseError, OSError, UnicodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return settings.rejected_exit_code if settings is not None else 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
