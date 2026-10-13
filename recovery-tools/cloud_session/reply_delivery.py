#!/usr/bin/env python3
"""Submit an observed connector response and advance a workspace-recover delivery."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--sequence', required=True)
    parser.add_argument('--step', required=True)
    parser.add_argument('--result', required=True, type=Path)
    parser.add_argument('--download', type=Path)
    args = parser.parse_args()
    root = args.run.resolve()
    config = json.loads((root/'RUN.json').read_text())
    for name in (args.sequence, args.step):
        if Path(name).name != name:
            parser.error('Sequence and step must be basenames')
    base = ['node', str(Path(config['workspace_recover_root'])/'bin/workspace-recover.mjs'), 'flow']
    session = root/(args.sequence+'-delivery-session')
    command = base+['reply', '--session', str(session), '--result', str(args.result.resolve())]
    if args.download:
        command += ['--path', str(args.download.resolve())]
    output = subprocess.run(command, capture_output=True, text=True, check=True)
    (root/'evidence'/f'{args.sequence}-{args.step}-replied.json').write_text(output.stdout)
    result = subprocess.run(base+['run','--session',str(session)], capture_output=True, text=True)
    (root/'evidence'/f'{args.sequence}-after-{args.step}.json').write_text(result.stdout)
    if result.returncode not in (0, 11):
        raise SystemExit(result.stderr or result.stdout)
    state=json.loads(result.stdout)
    if state['status']=='COMPLETED':
        (root/'evidence'/f'{args.sequence}-delivery-completed.json').write_text(result.stdout)
        print(result.stdout)
        return
    claim=subprocess.run(base+['host-claim','--session',str(session)], capture_output=True, text=True, check=True)
    print(claim.stdout)

if __name__ == '__main__':
    main()
