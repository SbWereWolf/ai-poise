#!/usr/bin/env python3
"""Explicit inspection/replay of optional AI-poise telemetry; no task operations."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from poise.common import PoiseError, load_config, configured_root, descendant
from poise.infrastructure.telemetry import TelemetryDatabase
from poise.infrastructure.telemetry_spool import TelemetrySpool
from poise.infrastructure.sqlite.accounting import SqliteAccounting
from poise.modules.accounting.domain import MetricPolicy


def configured_spool(config):
    root, cfg, _ = load_config(config)
    if 'telemetry_delivery' not in cfg:
        raise PoiseError('telemetry_delivery is not configured')
    state = configured_root(root, cfg['paths']['state'])
    storage, limits = cfg['accounting']['storage'], cfg['limits']
    repository = SqliteAccounting(TelemetryDatabase(
        descendant(state, storage['database']), descendant(state, storage['lock']),
        limits['lock_seconds'], limits['lock_poll_seconds']),
        cfg['project'], MetricPolicy.parse(cfg['accounting']))

    setting = cfg['telemetry_delivery']
    return TelemetrySpool(state / setting['directory'], setting['policy'], repository.accept_telemetry)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('operation', choices=('status', 'replay'))
    args = parser.parse_args()
    try:
        spool = configured_spool(args.config)
        value = spool.status() if args.operation == 'status' else spool.replay()
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 1 if value.get('status') == 'unavailable' or value.get('failed') or value.get('corrupt') else 0
    except (PoiseError, OSError, ValueError) as exc:
        print(json.dumps({'status': 'rejected', 'error': type(exc).__name__, 'detail': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
