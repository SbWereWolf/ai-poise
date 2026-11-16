"""Independent observation and content use cases; one bounded last-good read."""
from copy import deepcopy
import sqlite3
from domain.generic import Problem,exact_fields,invalid
from domain.observations import encoded,canonical,limit
from domain.measurement import line_delta
from infrastructure.tokenizer import Tokenizer


class ObservationOperations:
    def __init__(self,store=None,sessions=None,measurement=None):
        self.store,self.sessions,self.measurement=store,sessions,measurement
        self._last_key,self._last_result=None,None

    def prepare(self,value):
        exact_fields(value,set(),'/input')
        return self.store.prepare()

    def record(self,value):
        exact_fields(value,{'events'},'/input')
        session=self.sessions().status()['session']
        try:return self.store.record(value['events'],session['id'],closed=session['status']=='closed')
        except sqlite3.Error:self.store.unavailable('storage')

    def read(self,value):
        exact_fields(value,{'scope','source','after','limit'},'/input')
        scope=exact_fields(value['scope'],{'kind'},'/input/scope')['kind']
        if scope not in ('installation','session'):invalid('/input/scope/kind')
        if value['source'] is not None and (not isinstance(value['source'],str) or value['source'] not in self.store.settings.sources):invalid('/input/source')
        if type(value['after']) is not int or value['after']<0:invalid('/input/after')
        if type(value['limit']) is not int or value['limit']<=0:invalid('/input/limit')
        if value['limit']>self.store.settings.limits['max_results']:limit('max_results','reduce:read')
        session=self.sessions().status()['session']['id'] if scope=='session' else None
        key=canonical([value,session])
        try:result=self.store.read(value,session)
        except Problem as error:
            if error.error['code']!='observations_unavailable':raise
            if self._last_key==key:
                result=deepcopy(self._last_result);result.update(availability='cached',coverage='partial')
            else:
                result={'schema':'poise-observations-read/v1','scope':value['scope'],'source':value['source'],
                        **{k:None for k in ('as_of_seq','events','observed_events','usage_events','totals','next_after','has_more')},
                        'availability':'unavailable','coverage':'unavailable'}
            raise Problem(error.error['code'],error.error['details'],error.error['action'],'unavailable',result) from None
        if len(encoded(result).encode())>self.store.settings.limits['max_read_response_bytes']:limit('max_read_response_bytes','reduce:read')
        self._last_key,self._last_result=key,deepcopy(result)
        return result

    def measure(self,value):
        exact_fields(value,{'before','after'},'/input')
        for key in ('before','after'):
            if not isinstance(value[key],str):invalid('/input/'+key)
        try:size=sum(len(value[k].encode()) for k in ('before','after'))
        except UnicodeError:invalid('/input')
        if size>self.measurement.max_content_bytes:limit('max_content_bytes','reduce:content')
        delta=line_delta(value['before'],value['after']);instrument=self.measurement.tokenizer
        result={'schema':'poise-content-measure/v1',**{k:delta[k] for k in ('added_lines','removed_lines','added_bytes','removed_bytes')},
                'changed_lines':delta['added_lines']+delta['removed_lines'],'changed_bytes':delta['added_bytes']+delta['removed_bytes'],
                'added_tokens':None,'removed_tokens':None,'changed_tokens':None,
                'tokenizer':{'identity':instrument['identity'],'available':False},'coverage':'partial'}
        failure=None
        if instrument['kind']=='command':
            try:
                added,removed=Tokenizer(instrument['command']).count([delta['added_text'],delta['removed_text']])
                result.update(added_tokens=added,removed_tokens=removed,changed_tokens=added+removed,coverage='complete')
                result['tokenizer']['available']=True
            except (OSError,ValueError,TimeoutError) as exc:failure=type(exc).__name__
        if len(encoded(result).encode())>self.measurement.max_response_bytes:limit('max_response_bytes','reduce:content')
        if failure:raise Problem('measurement_unavailable',{'reason':failure},'configure:measurement','unavailable',result)
        return result
