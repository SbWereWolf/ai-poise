"""Ordered event delivery through capability/process and saved-delivery ports."""
from copy import deepcopy

from ..modules.foundation.errors import PoiseError
from ..modules.hook_transport.dispatch import HandlerResult
from ..modules.hook_transport.ports import HandlerExecutor, DispatchDeliveryStore


class HookDispatcher:
    def __init__(self, store: DispatchDeliveryStore, executor: HandlerExecutor):
        self.store, self.executor = store, executor

    def dispatch(self, path):
        delivery = self.store.read(path)
        if delivery['chain_complete']:
            return deepcopy(delivery['response'])
        response = deepcopy(delivery['response'])
        observed = {item['invocation_id']: item for item in delivery['invocations']}
        for planned in delivery['planned_invocations']:
            invocation = observed.get(planned['invocation_id'], planned)
            status = invocation['status']
            if invocation['mode'] == 'async' and invocation.get('launched') is True:
                continue
            if status == 'completed':
                if invocation['mode'] == 'sync':
                    response = {**response, **deepcopy(invocation['result']['response'])}
                    if invocation['result']['control']['action'] == 'stop':
                        break
                continue
            if status != 'prepared':
                raise PoiseError('Saved invocation outcome is not launchable; read the '
                                 'existing delivery through hook --retry without a new event')
            identifier = invocation['invocation_id']
            self.store.begin_invocation(path, planned)
            packet = {'schema': 'poise-hook-handler-input-1', 'invocation_id': identifier,
                      'mode': invocation['mode'], 'event': deepcopy(delivery['event']),
                      'event_receipt': deepcopy(delivery['event_receipt']),
                      'configuration': deepcopy(invocation['configuration']),
                      'response': deepcopy(response)}
            handler = invocation['handler']
            directory = self.store.invocation_directory(path, identifier)
            self.store.save_input(directory, packet)
            execution = None
            try:
                if invocation['mode'] == 'async':
                    self.executor.launch(handler, packet, directory, path)
                    continue
                # Persist before entering the effect. A killed author never
                # turns an uncertain external effect back into prepared.
                self.store.update_invocation(path, identifier, status='running')
                raw, execution = self.executor.execute(handler, packet, directory,
                                                       resources=invocation['resources'])
                result = HandlerResult.parse(raw, identifier)
                response = {**response, **deepcopy(result['response'])}
                self.store.update_invocation(path, identifier, status='completed',
                                             execution=execution, result=result)
                if result['control']['action'] == 'stop':
                    break
            except (PoiseError, OSError, ValueError, TypeError, KeyError) as exc:
                changes = {'status': getattr(exc, 'outcome', 'failed'),
                           'error': {'kind': type(exc).__name__, 'detail': str(exc)}}
                execution = getattr(exc, 'execution', execution)
                if execution is not None:
                    changes['execution'] = execution
                self.store.update_invocation(path, identifier, **changes)
                self.store.save_response(path, response, failed=True)
                return response
        self.store.save_response(path, response, failed=False)
        return response
