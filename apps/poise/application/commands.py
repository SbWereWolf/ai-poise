"""Own Session command request, completion and explicit data retirement."""
from copy import deepcopy
import time

from domain.command import absent, identity_input, request_input, same_json, error
from domain.command_config import execution_settings
from domain.generic import Problem


class CommandOperations:
    def __init__(self,sessions,settings,store,launcher,files):
        self.sessions,self.settings,self.store,self.launcher=sessions,settings,store,launcher
        self.files=files

    def session(self):
        return self.sessions.observed(self.sessions.caller(create=False))

    @staticmethod
    def deliver(record,operation,replayed=False):
        meta=deepcopy(record['metadata'])
        meta.update(operation=operation,replayed=replayed)
        if meta['error'] is not None:
            fault=meta['error']
            raise Problem(fault['code'],fault['details'],fault['action'],'unavailable',meta)
        return meta

    def request(self,value):
        request_input(value,self.settings)
        session=self.session()
        settings=execution_settings(self.settings)
        if session.status=='closed':
            existing=self.store.read(session.id,value['command_id'])
            if existing is not None and same_json(existing['input'],value) and same_json(existing['settings'],settings):
                return self.deliver(existing,'request',True)
            raise Problem('session_closed',{'status':'closed'},'select:session')
        record,created=self.store.admit(session.id,value,settings)
        if created:
            try: self.launcher.start(self.store,session.id,value['command_id'])
            except OSError:
                meta=deepcopy(record['metadata'])
                meta.update(state='UNKNOWN',error=error('command_observation_lost',{'phase':'dispatch'},'inspect:command'))
                self.store.publish(session.id,value['command_id'],meta)
            record=self.store.read(session.id,value['command_id'])
        return self.deliver(record,'request',not created)

    def completion(self,value):
        id=identity_input(value,self.settings)
        session=self.session()
        budget=self.settings.limits['completion_budget_ms']
        end=time.monotonic()+budget/1000
        while True:
            try:
                record=self.store.read(session.id,id)
            except Problem as exc:
                remaining=end-time.monotonic()
                if exc.error['code']!='command_store_busy' or remaining<=0:
                    raise
                time.sleep(min(remaining,self.settings.limits['poll_ms']/1000))
                continue
            if record is None:
                raise Problem('command_not_found',{'command_id':id},'use:command','unavailable')
            if record['metadata']['state'] in ('TERMINAL','UNKNOWN'):
                return self.deliver(record,'completion')
            remaining=end-time.monotonic()
            if remaining<=0:
                meta=deepcopy(record['metadata']);meta.update(operation='completion',replayed=False)
                raise Problem('command_wait_expired',{'command_id':id,'budget_ms':budget},'wait:command','unavailable',meta)
            time.sleep(min(remaining,self.settings.limits['poll_ms']/1000))

    def delete(self,value):
        id=identity_input(value,self.settings)
        session=self.session()
        record=self.store.read(session.id,id)
        if record is not None:
            self.files(record).remove(id)
            self.store.delete(session.id,id)
        return absent(id)
