"""Publish an explicitly configured maintenance profile, never serviced state."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from poise.common import PoiseError
from poise.modules.projects.domain import ProjectBlueprint
from .shared.templates import render_data


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_inputs(args, source):
    selected = Path(
        args.profile_spec) if args.profile_spec else source / "config/maintenance/profile.example.json"
    specification = read_json(selected)
    if specification["schema"] != "poise-maintenance-profile-1":
        raise ValueError("Unsupported profile specification")
    catalog = read_json(source / specification["catalog"])
    if catalog["schema"] != "environment-maintenance/catalog/v2":
        raise ValueError("Selected example must declare catalog/v2")
    settings = read_json(source / specification["settings"])
    template_path = specification["templates"][args.template]
    blueprint = read_json(source / template_path)
    recommendations = read_json(
        source / specification["recommendations"])
    return specification, catalog, settings, template_path, blueprint, recommendations


def bind_parameters(args, catalog, output):
    values = {name: declaration["default"]
              for name, declaration in catalog["parameters"].items()
              if "default" in declaration}
    selected = {"python": sys.executable,
                "module_path": args.module_path,
                "profile_root": str(output),
                "state_root": str(Path(args.state).absolute()),
                "repository": str(Path(args.repository).absolute()),
                "project_id": args.project, "base_ref": args.base_ref,
                "author_name": args.author_name,
                "author_email": args.author_email,
                "template_id": args.template}
    for name, value in selected.items():
        if name not in catalog["parameters"]:
            raise ValueError(
                f"Profile catalog lacks parameter: {name}")
        catalog["parameters"][name]["default"] = value
        values[name] = value
    return values


def prepare_documents(specification, source, values):
    documents = []
    for projection in specification["documents"]:
        document = read_json(source / projection["source"])
        for edit in projection["edits"]:
            parent = document
            for part in edit["path"][:-1]:
                parent = parent[part]
            parent[edit["path"][-1]] = render_data(edit["value"],
                                                   values)
        documents.append((projection["destination"], document))
    return documents


def copy_assets(specification, source, stage, values):
    for asset in specification["asset_directories"]:
        asset = render_data({key: asset[key] for key in
                             ("source", "destination", "patterns")},
                            values)
        origin = source / asset["source"]
        for pattern in asset["patterns"]:
            matches = list(origin.glob(pattern))
            if not matches:
                raise ValueError(
                    f"Required asset pattern is empty: {pattern}")
            for selected in matches:
                destination = stage / asset[
                    "destination"] / selected.relative_to(origin)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(selected, destination)


def prepare_profile(stage, source, output, args, inputs):
    spec, catalog, settings, template_path, blueprint, advice = inputs
    values = bind_parameters(args, catalog, output)
    outputs = render_data(spec["outputs"], values)
    if outputs["catalog"] != "catalog.json":
        raise ValueError("The selected consumer reads catalog.json")
    write = prepare_project_profile(stage, source, output, args, spec,
                                    settings, template_path, blueprint, values)
    write(outputs["catalog"], catalog)
    write(outputs["recommendations"], advice)
    return catalog


def prepare_project_profile(stage, source, output, args, spec, settings,
                            template_path, blueprint, values):
    """Project publication only; resource effects stay with maintenance owners."""
    outputs = render_data(spec["outputs"], values)
    edits = render_data(spec["project_edits"], values)
    projected_documents = prepare_documents(spec, source, values)
    ProjectBlueprint.parse(blueprint).build(edits, settings["max_edits"])
    stage.mkdir(mode=settings["directory_mode"])

    def write(relative, document):
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, ensure_ascii=False,
                                   indent=settings[
                                       "json_indent"]) + "\n",
                        encoding="utf-8")
        path.chmod(settings["file_mode"])

    copy_assets(spec, source, stage, values)
    prefix = spec["workflow_source_prefix"]
    for reference in blueprint["workflow_sources"].values():
        if not reference["path"].startswith(prefix):
            raise ValueError(
                "Workflow source does not match the explicit profile source mapping")
        relative = reference["path"][len(prefix):]
        document = read_json(source / relative)
        reference["path"] = relative
        write(relative, document)
    write(template_path, blueprint)
    settings.update(render_data(spec["settings_overrides"], values))
    settings["templates"] = {args.template: {"path": template_path}}
    for destination, document in projected_documents:
        write(destination, document)
    for relative in spec["preserve_paths"]:
        existing, destination = output / relative, stage / relative
        if existing.is_dir():
            shutil.copytree(existing, destination)
        elif existing.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(existing, destination)
    write(outputs["settings"], settings)
    if not (stage / outputs["registry"]).exists():
        write(outputs["registry"],
              {"schema": "configured-project-registry-1",
               "projects": {}})
    write(outputs["edits"], edits)
    return write


def publish_profile(stage, output, previous):
    replaced = output.exists()
    if replaced:
        os.replace(output, previous)
    try:
        os.replace(stage, output)
    except OSError:
        if replaced:
            os.replace(previous, output)
        raise
    return replaced


def generate(args):
    source, output = Path(args.source).absolute(), Path(
        args.output).absolute()
    if not output.parent.is_dir():
        raise ValueError("Profile parent directory must exist")
    selected = Path(args.profile_spec) if args.profile_spec else source / "config/maintenance/profile.example.json"
    specification = read_json(selected)
    schema = specification["schema"]
    if schema == "poise-start-app-profile-1":
        from .start_app_profile import prepare_profile as prepare_operations
        prepare = lambda stage: prepare_operations(stage, source, output, args, specification)
    elif schema == "poise-maintenance-profile-1":
        inputs = load_inputs(args, source)
        prepare = lambda stage: prepare_profile(stage, source, output, args, inputs)
    else:
        raise ValueError("Unsupported profile specification")
    with tempfile.TemporaryDirectory(prefix=".profile-",
                                     dir=output.parent) as raw:
        working = Path(raw)
        catalog = prepare(working / "profile")
        replaced = publish_profile(working / "profile", output,
                                   working / "previous")
    return {"status": "replaced" if replaced else "created",
            "profile": str(output),
            "requirements": len(catalog["requirements"]),
            "state_created": False}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Развернуть профиль обслуживания Poise")
    for name in ("source", "output", "state", "repository", "project",
                 "base-ref", "author-name", "author-email"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--template", default="common")
    parser.add_argument("--module-path", default="")
    parser.add_argument("--profile-spec")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(generate(args), ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, PoiseError) as error:
        print(json.dumps({"status": "invalid", "error": str(error)},
                         ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
