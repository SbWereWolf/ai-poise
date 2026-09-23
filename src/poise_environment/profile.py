"""Materialize declarative deployment assets without creating application state."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from environment_maintenance.catalogue import load_catalogue, read_json
from environment_maintenance.parameters import resolve
from environment_maintenance.repair import RepairRouter
from environment_maintenance.shared.filesystem import lexical_path
from poise.common import digest


def generate(args):
    source = lexical_path(args.source)
    output = lexical_path(args.output)
    if output.exists():
        raise ValueError('Profile destination already exists; configuration is never replaced')
    if not output.parent.is_dir():
        raise ValueError('Profile parent directory must exist')
    settings = read_json(source / 'config/project-setup.json')
    selection = settings['templates'][args.template]
    blueprint = read_json(source / selection['path'])
    if digest(blueprint) != selection['digest']:
        raise ValueError('Source project template digest mismatch')
    state = str(lexical_path(args.state))
    repository = str(lexical_path(args.repository))
    catalog = load_catalogue(source / 'config/maintenance/requirements.json').data
    repairs_path = source / 'config/maintenance/repairs.json'
    repairs = read_json(repairs_path)
    # Only initial parameter values are materialized here. The requirement
    # inventory, commands and recommendation routing belong to versioned JSON.
    values = {
        'python': sys.executable, 'module_path': args.module_path,
        'profile_root': str(output), 'state_root': state,
        'repository': repository, 'project_id': args.project,
        'base_ref': args.base_ref, 'author_name': args.author_name,
        'author_email': args.author_email, 'template_id': args.template,
    }
    for name, value in values.items():
        if name not in catalog['parameters']:
            raise ValueError(f'Profile catalog lacks parameter: {name}')
        catalog['parameters'][name]['default'] = value
    parameters, _ = resolve(catalog['parameters'])
    router = RepairRouter(repairs_path, parameters)
    for requirement in catalog['requirements']:
        for mode in ('check', 'apply', 'recommendation'):
            action = requirement[mode]
            if isinstance(action, dict) and 'repair' in action:
                router.require(action['repair'])
    with tempfile.TemporaryDirectory(prefix='.profile-', dir=output.parent) as tmp:
        stage = Path(tmp) / 'profile'
        stage.mkdir(mode=0o750)
        def write(path, value):
            destination = stage / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
            destination.chmod(0o600)
        write(selection['path'], blueprint)
        for reference in blueprint['process_sources'].values():
            document = read_json(source / reference['path'])
            if digest(document) != reference['digest']:
                raise ValueError('Process template digest mismatch')
            write(reference['path'], document)
        settings.update(root='.', templates={args.template: selection},
                        registry='configured-projects.json', lock='setup.lock')
        write('setup.json', settings)
        write('configured-projects.json',
              {'schema': 'configured-project-registry-1', 'projects': {}})
        write('requirements.json', catalog)
        write('repairs.json', repairs)
        write('values.example.json', {
            'state_root': state, 'repository': repository,
            'project_id': args.project,
        })
        if output.exists():
            raise ValueError('Profile destination appeared during creation')
        os.rename(stage, output)
    return {'status': 'created', 'profile': str(output),
            'requirements': len(catalog['requirements']), 'state_created': False}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Создать профиль обслуживания Poise из конфигов поставки')
    for name in ('source', 'output', 'state', 'repository', 'project',
                 'base-ref', 'author-name', 'author-email'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--template', default='linux-reference')
    parser.add_argument('--module-path', default='')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(generate(args), ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'invalid', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
