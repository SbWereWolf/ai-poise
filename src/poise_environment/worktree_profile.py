"""Publish declared preparation inputs; delegate resource effects to the SDK.

The native bridge binds invocation coordinates. Document publication reuses the
profile owner's projections, asset copying and atomic directory publication.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile

from poise_environment.profile import (copy_assets, prepare_documents,
                                       publish_profile, read_json)
from poise_environment.shared.templates import render_data


def publish_documents(specification, source, output, parameters):
    source, output = Path(source), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    projections = dict(specification, documents=[
        dict(item, source=render_data(item["source"], parameters))
        for item in specification["documents"]])
    documents = prepare_documents(projections, source, parameters)
    with tempfile.TemporaryDirectory(prefix=".profile-", dir=output.parent) as raw:
        stage = Path(raw) / "profile"
        stage.mkdir()
        for relative in specification["preserve_paths"]:
            existing, destination = output / relative, stage / relative
            if existing.is_dir():
                shutil.copytree(existing, destination, dirs_exist_ok=True)
            elif existing.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(existing, destination)
        copy_assets(specification, source, stage, parameters)
        for destination, document in documents:
            path = stage / render_data(destination, parameters)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        for declaration in specification["text_documents"]:
            path = stage / render_data(declaration["destination"], parameters)
            path.parent.mkdir(parents=True, exist_ok=True)
            text = (source / declaration["source"]).read_text(encoding="utf-8")
            path.write_text(render_data(text, parameters), encoding="utf-8")
        publish_profile(stage, output, Path(raw) / "previous")


def invocation_parameters(coordinates, selection):
    names = ("source_root", "python", "start_app", "project_id", "repository",
             "task_root", "profile_root", "base_ref", "selected_values")
    values = dict(selection["parameters"])
    values.update(zip(names, coordinates, strict=True))
    for name, template in selection["derived_parameters"].items():
        values[name] = render_data(template, values)
    return values


def prepare(coordinates):
    selection = read_json(Path(coordinates[-1]))
    parameters = invocation_parameters(coordinates, selection)
    path = Path(parameters["supervisor_parameters"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(parameters, ensure_ascii=False, indent=2) + "\n")
    command = render_data(selection["prepare_command"], parameters)
    environment = render_data(selection["prepare_environment"], parameters)
    os.chdir(parameters["repository"])
    # Replace this bridge; the generic SDK owns the configured command chain.
    os.execve(command[0], command, environment)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Опубликовать заданный профиль подготовки")
    commands = parser.add_subparsers(dest="operation", required=True)
    invocation = commands.add_parser("prepare")
    invocation.add_argument("coordinates", nargs=9)
    publication = commands.add_parser("publish")
    for name in ("spec", "source", "output", "parameters"):
        publication.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    if args.operation == "prepare":
        prepare(args.coordinates)
    else:
        publish_documents(read_json(Path(args.spec)), args.source, args.output,
                          read_json(Path(args.parameters)))
        print(json.dumps({"status": "published", "profile": args.output}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
