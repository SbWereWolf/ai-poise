"""Read-only composition for explicit hook observations; no runtime bootstrapping."""
from pathlib import Path
import platform
import sqlite3
import sys

from ..common import PoiseError, digest, descendant, configured_root, load_config
from ..modules.hook_transport.domain import HookDefinition, EVENTS
from .diagnostic_io import json_file, regular_bytes
from .source_reader import FileSourceReader
from .sqlite.interactions import InteractionStore
from .sqlite.evidence import SqliteEvidenceRepository
from .telemetry_spool import TelemetrySpool

_ERRORS = (PoiseError, OSError, ValueError, TypeError, KeyError, sqlite3.Error)


def _unavailable(exc):
    # Do not echo store contents, raw commands, messages or credentials.
    return {'status': 'unavailable', 'error': type(exc).__name__}


class HookObservationPort:
    def __init__(self, service):
        self.service, self.settings = service, service.settings

    def validate_paths(self, req):
        path = Path(req['definition_path'])
        if (not path.is_absolute() or path.is_symlink()
                or not path.resolve().is_relative_to(self.settings.definitions)):
            raise PoiseError('Definition must name the installed definition path')
        if req['probe_cwd'] is not None:
            path = Path(req['probe_cwd']).resolve(strict=True)
            if not path.is_dir():
                raise PoiseError('probe_cwd must be a concrete existing directory')

    def _definition(self, req):
        path = Path(req['definition_path'])
        value = json_file(path, self.settings.raw['max_input_bytes'])
        if path.stem != digest(value):
            raise PoiseError('Hook definition digest mismatch')
        return HookDefinition.parse(value)

    def probes(self, req):
        try:
            self._definition(req)
            return self.service.probes(req['definition_path'], str(Path(req['probe_cwd']).resolve(strict=True)))
        except _ERRORS as exc:
            return {**_unavailable(exc), 'ready': False}

    def _installation(self, saved, definition, path):
        if saved is None:
            return {'status': 'not_observed'}
        if not isinstance(saved, dict):
            raise PoiseError('Invalid installation observation')
        expected = self.service.repository._groups(definition, Path(path))
        receipt = saved.get('receipt', {})
        if not isinstance(receipt, dict):
            raise PoiseError('Invalid installation receipt')
        if (saved.get('groups') != expected or receipt.get('definition_path') != path
                or receipt.get('hooks_path') != str(self.settings.hooks_file)):
            return {'status': 'changed'}
        actual = json_file(self.settings.hooks_file, self.settings.raw['max_input_bytes'])
        hooks = actual.get('hooks', {})
        if not isinstance(hooks, dict):
            return {'status': 'changed'}
        for event, group in expected:
            groups = hooks.get(event, [])
            if not isinstance(groups, list) or groups.count(group) != 1:
                return {'status': 'changed'}
        return {'status': 'configured', 'definition_id': definition.data['id'],
                'definition_path': path, 'host_trust': 'not_observed'}

    def _binding(self, saved, definition, req, cfg):
        if saved is None:
            return {'status': 'not_observed'}
        if not isinstance(saved, dict) or not isinstance(saved.get('external_session'), str):
            raise PoiseError('Invalid binding observation')
        expected = {'session_id': req['session_id'], 'settings': str(self.settings.path),
                    'settings_digest': digest(self.settings.raw), 'definition_path': req['definition_path'],
                    'project': cfg['project'], 'agent_id': definition.data['agent_id']}
        if any(saved.get(k) != v for k, v in expected.items()):
            return {'status': 'changed'}
        # Binding paths come from the existing owner's record, never caller session concatenation.
        binding_path, launcher = Path(saved['binding_path']), Path(saved['launcher'])
        for path in (binding_path, launcher):
            if (not path.is_absolute() or path.is_symlink()
                    or not path.resolve().is_relative_to(self.settings.bindings)):
                return {'status': 'changed'}
        if binding_path.parent != launcher.parent:
            return {'status': 'changed'}
        if json_file(binding_path, self.settings.raw['max_input_bytes']) != saved:
            return {'status': 'changed'}
        expected_script = ('#!/bin/sh\nexec ' + self.settings.command('hook-work', '--binding', str(binding_path)) + '\n').encode()
        if (regular_bytes(launcher, self.settings.raw['max_input_bytes']) != expected_script
                or launcher.stat().st_mode & 0o777 != self.settings.raw['executable_mode']):
            return {'status': 'changed'}
        if cfg['batch']['message_source'] != {'id': definition.data['message_source'], 'mode': 'runtime_event'}:
            return {'status': 'changed'}
        return {'status': 'valid', 'session_id': saved['session_id']}

    def _effect(self, req, binding, event, root, cfg):
        state = configured_root(root, cfg['paths']['state'])
        database = descendant(state, cfg['paths']['database'])
        kind = req['event']
        if kind == 'SessionStart':
            if 'source_reader' not in cfg:
                return {'owner': 'SourceReader', 'status': 'not_configured'}
            reader = cfg['source_reader']
            directory = descendant(descendant(state, cfg['paths']['runtime']), req['session_id'])
            policy = json_file((root / reader['policy']).resolve(), self.settings.raw['max_input_bytes'])
            observed = FileSourceReader(descendant(directory, reader['receipt_file']), policy).observe_context()
            return {'owner': 'SourceReader', 'status': 'observed', 'context': observed}
        if kind == 'UserPromptSubmit':
            turn = event['data'].get('turn_id')
            if not isinstance(turn, str) or not turn:
                raise PoiseError('Native prompt has no source turn identity')
            message = InteractionStore.observe_message(database, cfg['project'], cfg['batch']['message_source'],
                        binding['external_session'], turn, self.settings.raw['max_input_bytes'])
            return {'owner': 'InteractionStore', 'status': 'not_observed' if message is None else 'observed',
                    'message': message}
        if kind == 'SessionEnd':
            if not req['run_ids']:
                return {'owner': 'SqliteEvidenceRepository', 'status': 'not_observed', 'runs': []}
            runs = SqliteEvidenceRepository.observe_runs(database, req['run_ids'], self.settings.raw['max_input_bytes'])
            for run in runs:
                corr = run.get('cancellation_observation')
                start = corr.get('start_event_id') if isinstance(corr, dict) else None
                run['start_observed'] = bool(type(start) is int and start > 0 and
                    self.service.registry.observed_start(req['session_id'], start, event['id']))
            return {'owner': 'SqliteEvidenceRepository', 'status': 'observed', 'runs': runs}
        return {'owner': 'HookService', 'status': 'not_recorded',
                'reason': 'Native Stop output delivery has no durable owner receipt'}

    def _telemetry(self, root, cfg):
        if 'telemetry_delivery' not in cfg:
            return {'status': 'not_configured', 'scope': 'configured_spool_not_selected_event'}
        state = configured_root(root, cfg['paths']['state']); delivery = cfg['telemetry_delivery']
        status = TelemetrySpool(state / delivery['directory'], delivery['policy'], None).status()
        # The complete queue metadata already belongs to the existing spool status operation.
        return {**{k: v for k, v in status.items() if k != 'records'},
                'scope': 'configured_spool_not_selected_event', 'pre_persistence_loss_window': True}

    def snapshot(self, req):
        result = {'schema': 'poise-hook-diagnostics-1', 'host_trust': 'not_observed',
                  'installation': {'status': 'not_observed'}, 'binding': {'status': 'not_observed'},
                  'native_event': {'status': 'not_observed'}, 'effect': {'status': 'not_observed'},
                  'telemetry_delivery': {'status': 'not_observed'},
                  'runtime': {'interpreter': sys.executable, 'python_version': platform.python_version(),
                              'package_location': str(Path(__file__).resolve().parents[1]),
                              'provenance': 'current_diagnostic_process_not_historical_hook'},
                  'consistency': 'independent_owner_snapshots'}
        try:
            definition = self._definition(req)
            root, cfg, _ = load_config(self.settings.project_config)
            snapshot = self.service.registry.observe(definition.data['id'], req['session_id'], req['event'], req['event_id'])
        except FileNotFoundError:
            return result
        except _ERRORS as exc:
            result['native_event'] = _unavailable(exc)
            return result
        try:
            result['installation'] = self._installation(snapshot['installation'], definition, req['definition_path'])
            result['binding'] = self._binding(snapshot['binding'], definition, req, cfg)
        except _ERRORS as exc:
            result['binding'] = _unavailable(exc)
        allowed = req['event'] in EVENTS and req['event'] in {e['event'] for e in definition.data['events']}
        event = snapshot['event']
        if not allowed:
            result['native_event'] = {'status': 'unsupported', 'event': req['event']}
        elif event is not None:
            if not isinstance(event['data'], dict):
                result['native_event'] = {'status': 'unavailable', 'error': 'InvalidEventData'}
            else:
                result['native_event'] = {'status': 'observed', 'id': event['id'], 'event': event['event'],
                    'session_id': event['binding'], 'recorded_at': event['at'],
                    'source': event['data'].get('source'), 'turn_id': event['data'].get('turn_id')}
                if result['binding']['status'] == 'valid':
                    try:
                        result['effect'] = self._effect(req, snapshot['binding'], event, root, cfg)
                    except FileNotFoundError:
                        result['effect'] = {'status': 'not_observed'}
                    except _ERRORS as exc:
                        result['effect'] = _unavailable(exc)
        try:
            result['telemetry_delivery'] = self._telemetry(root, cfg)
        except _ERRORS as exc:
            result['telemetry_delivery'] = _unavailable(exc)
        return result
