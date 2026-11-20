"""One configured pytest call and its unchanged reporter; no retries or scheduler."""
import argparse
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tests.skills.record_instruction_red import run

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['controls', 'proxy', 'all'], required=True)
    group = parser.parse_args().group
    names = {
        'controls': 'test_post_tool_proxy_controls.py',
        'proxy': 'test_initial_post_tool_proxy.py',
    }
    nodes = ['tests/hook_transport/' + names[key] for key in (names if group == 'all' else [group])]
    root = Path(os.environ['POISE_RUN_OUTPUT_DIR'])
    code, summary = run(nodes, root, 'post-tool-' + group)
    shutil.rmtree(root / 'pytest-temp')
    sys.stdout.write(summary)
    raise SystemExit(code)
