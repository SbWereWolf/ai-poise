"""Select only the new reviewed boundary group for the existing reporter."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.skills.record_instruction_red import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', required=True, choices=['reverse-records', 'nested-cwd'])
    group = parser.parse_args().group
    data = json.loads((Path(__file__).with_name('fixtures') /
                       'remaining-boundaries.json').read_text(encoding='utf-8'))
    actual, output = run(data['nodes'][group], Path(os.environ['POISE_RUN_OUTPUT_DIR']), group)
    sys.stdout.write(output)
    return actual


if __name__ == '__main__':
    raise SystemExit(main())
