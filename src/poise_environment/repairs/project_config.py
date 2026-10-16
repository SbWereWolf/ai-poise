"""Bridge to the existing project publication and configuration owner."""
import argparse
import json
from pathlib import Path

from poise.application.projects import ProjectCommands
from poise.common import PoiseError, load_config, digest
from poise.infrastructure.projects import ProjectSettings, \
    FileProjectSetup
from ..shared.protocol import Observation, serve


def handle(args):
    path = Path(args.config).absolute()
    edits = json.loads(Path(args.edits).read_text(encoding="utf-8"))
    if path.exists():
        try:
            _, configuration, _ = load_config(path)
        except PoiseError as error:
            return Observation("unavailable", "invalid_config",
                               str(error))
        for edit in edits:
            value = configuration
            for part in edit["path"]:
                value = value[part]
            if value != edit["value"]:
                return Observation("unavailable",
                                   "configuration_drift")
        return Observation("satisfied")
    if args.action == "check":
        return Observation("missing", "missing")
    settings = ProjectSettings(Path(args.settings))
    packet = {"schema": "project-setup-1",
              "request_id": "maintenance-" + digest(edits),
              "destination": args.destination,
              "template": {"id": args.template},
              "edits": edits, "probe_repository": False}
    try:
        ProjectCommands(FileProjectSetup(settings)).apply(packet)
    except PoiseError as error:
        return Observation("unavailable", "invalid_config",
                           str(error))
    if not path.is_file():
        return Observation("unavailable", "invalid_config",
                           "Owner published a different destination")
    return handle(
        argparse.Namespace(**{**vars(args), "action": "check"}))


def main():
    parser = argparse.ArgumentParser(
        description="Конфигурация выбранного проекта")
    for name in ("config", "settings", "edits", "destination",
                 "template"):
        parser.add_argument("--" + name, required=True)
    return serve(parser, handle)


if __name__ == "__main__":
    raise SystemExit(main())
