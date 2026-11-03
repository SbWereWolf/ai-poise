"""Isolated child: only the installed prefix admits the selected Poise supply."""
import json
from pathlib import Path
import runpy
import sys


def main():
    prefix, origins_path, mode, *cli_args = sys.argv[1:]
    sys.path.insert(0, prefix)
    try:
        if mode == 'api':
            from poise.instructions import execute
            value = execute(json.load(sys.stdin))
            print(json.dumps(value, ensure_ascii=False))
            return 0
        sys.argv = ['poise', 'skills', *cli_args]
        runpy.run_module('poise', run_name='__main__')
        return 0
    finally:
        origins = {}
        for name, module in tuple(sys.modules.items()):
            if name == 'poise' or name.startswith(('poise.', 'poise_environment', 'apps.bootstrap')):
                origin = getattr(module, '__file__', None)
                if origin is not None:
                    origins[name] = str(Path(origin).resolve())
        Path(origins_path).write_text(json.dumps(origins, sort_keys=True))


if __name__ == '__main__':
    raise SystemExit(main())
