"""Publish the declared operation profile without preparing serviced resources."""
import os
from pathlib import Path
import sys

from .profile import read_json, prepare_project_profile
from .shared.templates import render_data


def bind_parameters(args, specification, output):
    values = dict(specification["parameters"])
    values.update(python=sys.executable, module_path=args.module_path,
                  profile_root=str(output), state_root=str(Path(args.state).absolute()),
                  repository=str(Path(args.repository).absolute()),
                  project_id=args.project, base_ref=args.base_ref,
                  author_name=args.author_name, author_email=args.author_email,
                  template_id=args.template, path_separator=os.pathsep)
    for name, template in specification["derived_parameters"].items():
        values[name] = render_data(template, values)
    return values


def prepare_profile(stage, source, output, args, specification):
    values = bind_parameters(args, specification, output)
    settings = read_json(source / specification["settings"])
    template = specification["templates"][args.template]
    blueprint = read_json(source / template)
    write = prepare_project_profile(stage, source, output, args, specification,
                                    settings, template, blueprint, values)
    # Operation argv templates belong to the SDK, which renders them at execution.
    # Copy their complete documents, including fields unknown to this publisher.
    for document in specification["operation_documents"]:
        write(document["destination"], read_json(source / document["source"]))
    bootstrap = render_data(read_json(source / specification["bootstrap"]), values)
    write(specification["outputs"]["bootstrap"], bootstrap)
    write(specification["outputs"]["parameters"], values)
    write(specification["outputs"]["recommendations"],
          read_json(source / specification["recommendations"]))
    return {"requirements": [resource for inventory in specification["resource_inventory"]
                             for resource in read_json(source / inventory)["requirements"]]}
