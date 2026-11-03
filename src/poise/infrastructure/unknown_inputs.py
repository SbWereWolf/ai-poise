"""Immutable unknown-input registry and non-waiting command delivery."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from ..common import PoiseError
from ..modules.result_views.unknown_inputs import UnknownObservation
from ..modules.result_views.tool_results import ToolResultFailure, encoded
from .file_publication import BinaryFilePublisher


class FileUnknownInputRegistry:
    def __init__(self, root, source_root, mode):
        self.root, self.source_root, self.mode = Path(root), Path(source_root), mode

    def register(self, value):
        observation = UnknownObservation(value)
        source = observation.data['full_source']
        path = Path(source['path'])
        if not path.resolve().is_relative_to(self.source_root.resolve()):
            raise ToolResultFailure('source_invalid', 'full_source', 'restore:source')
        try:
            if BinaryFilePublisher.read(path, 'Unknown full source') != source['sha256']:
                raise ToolResultFailure('source_invalid', 'full_source', 'restore:source')
        except ToolResultFailure:
            raise
        except (OSError, PoiseError) as error:
            raise ToolResultFailure('source_unreadable', 'full_source', 'restore:source') from error
        target = self.root / (observation.key() + '.json')
        try:
            BinaryFilePublisher.publish_bytes(observation.encoded(), target, self.mode)
        except (OSError, PoiseError) as error:
            raise ToolResultFailure('registry_write', 'unknown_inputs.registry_root', 'retry:register_unknown') from error
        return {'status': 'registered', 'observation_path': str(target)}


class CommandUnknownInputDispatcher:
    def __init__(self, policy, registry, environment):
        self.policy, self.registry = policy, registry
        self.environment = dict(environment) if environment else None

    def submit(self, value):
        observation = UnknownObservation(value)
        directory = Path(value['full_source']['path']).parent
        settings = self.policy.get('unknown_inputs', {})
        command = settings.get('registration_command') or [
            sys.executable, '-B', '-m', 'poise.infrastructure.unknown_inputs',
            '--registry-root', str(self.registry.root), '--source-root', str(self.registry.source_root),
            '--mode', str(self.registry.mode)]
        try:
            with (directory / 'unknown-registration.stdout').open('ab') as out, (
                    directory / 'unknown-registration.stderr').open('ab') as err:
                process = subprocess.Popen(command, cwd=settings.get('registration_cwd', str(directory)),
                    env=self.environment, stdin=subprocess.PIPE, stdout=out, stderr=err,
                    close_fds=True, start_new_session=True)
                try:
                    process.stdin.write(observation.encoded())
                finally:
                    process.stdin.close()
        except OSError as error:
            # The atomic result retains the observation; delivery stays bounded.
            try:
                with (directory / 'unknown-registration.stderr').open('a') as err:
                    err.write(f'Registration not started: {error}\nReplay register_unknown with the saved unknown_observation.\n')
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registry-root', required=True)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--mode', required=True, type=int)
    args = parser.parse_args()
    try:
        result = FileUnknownInputRegistry(args.registry_root, args.source_root, args.mode).register(json.load(sys.stdin))
        code = 0
    except ToolResultFailure as error:
        result, code = error.result(), 2
    except (PoiseError, OSError, ValueError):
        result = ToolResultFailure('invalid_request', 'observation', 'provide:request').result(); code = 2
    sys.stdout.write(encoded(result))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
