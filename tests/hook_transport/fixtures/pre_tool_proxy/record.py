"""One unchanged pytest report through the existing reporter, no retries."""
import argparse
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tests.skills.record_instruction_red import run


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['controls', 'proxy'], required=True)
    group = parser.parse_args().group
    nodes = ['tests/hook_transport/' + {
        'controls': 'test_pre_tool_proxy_controls.py',
        'proxy': 'test_initial_pre_tool_proxy.py',
    }[group]]
    root = Path(os.environ['POISE_RUN_OUTPUT_DIR'])
    code, summary = run(nodes, root, 'pre-tool-' + group)
    # All behavioral facts were extracted by assertions/the existing reporter.
    # Keep complete streams/events, retire only this invocation's fixture DBs.
    shutil.rmtree(root / 'pytest-temp')
    sys.stdout.write(summary)
    raise SystemExit(code)
