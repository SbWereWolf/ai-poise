"""Command-owned raw captures and atomic complete result-file publication."""
from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import uuid4

from domain.command import full_result
from domain.command_config import parse_settings
from domain.generic import Problem
from domain.state import invalid_config
from infrastructure.files import OutputStore


def configured_settings(raw,path,session_database):
    settings=parse_settings(raw)
    resolve=lambda value:(path.parent/Path(value)).resolve()
    store,output=resolve(settings.store),resolve(settings.output)
    if not store.parent.is_dir() or store==resolve(session_database):
        invalid_config('/commands/request_store')
    if not output.is_dir(): invalid_config('/commands/output_root')
    return replace(settings,store=str(store),output=str(output))


class CommandFiles:
    def __init__(self,record):
        self.record=record
        settings=record['settings']
        self.owner=OutputStore({'results_root':settings['output_root'],
                               'file_mode':settings['file_mode'],'files':settings['files']})
        self.paths={name:Path(settings['output_root'])/record['execution_id']/file
                    for name,file in settings['files'].items()}
        self.streams={}
        self.failed={}
        self.eof={'stdout':False,'stderr':False}

    def prepare(self):
        self.paths=self.owner.capture_paths(self.record['execution_id'])
        for name in ('stdout','stderr'):
            self.streams[name]=self.paths[name].open('wb',buffering=0)
            os.fchmod(self.streams[name].fileno(),self.owner.mode)

    def write(self,name,chunk):
        if name in self.failed: return
        try:
            view=memoryview(chunk)
            while view:
                count=self.streams[name].write(view)
                view=view[count:]
        except OSError as exc:
            self.failed[name]=exc.errno

    def close(self):
        for stream in self.streams.values(): stream.close()

    def sources(self):
        return {name:{'source_id':self.record[name+'_id'],'path':str(self.paths[name]),
                      'bytes':self.paths[name].stat().st_size,
                      'complete':self.eof[name] and name not in self.failed}
                for name in ('stdout','stderr')}

    def publish(self,metadata,mcp):
        value=full_result(metadata,mcp)
        raw=(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n').encode()
        target=self.paths['result']
        candidate=target.with_name('.result-'+uuid4().hex+'.json')
        try:
            with candidate.open('wb') as stream:
                os.fchmod(stream.fileno(),self.owner.mode)
                stream.write(raw)
            os.replace(candidate,target)
        finally:
            try: candidate.unlink()
            except FileNotFoundError: pass
        return {'path':str(target),'bytes':len(raw)}

    def remove(self,id):
        target=self.paths['stdout'].parent
        try:
            for name in ('stdout','stderr','result'):
                target=self.paths[name]
                try: target.unlink()
                except FileNotFoundError: pass
            target=self.paths['stdout'].parent
            try: target.rmdir()
            except FileNotFoundError: pass
        except OSError as exc:
            raise Problem('command_delete_failed',{'command_id':id,'path':str(target),'errno':exc.errno},
                          'inspect:command','unavailable') from None
