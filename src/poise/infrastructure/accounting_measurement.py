"""Git/SQLite source readers and optional exact tokenizer invocation."""
from __future__ import annotations
import fnmatch
import json
import os
import subprocess
from pathlib import Path
from ..common import PoiseError,encoded,digest,prohibit_git_push
from ..modules.accounting.domain import BenefitDefinition,line_delta


def zero_measure(tokens_available):
    return {'changed_lines':0,'changed_bytes':0,'changed_tokens':0 if tokens_available else None,
            'categories':{},'sections':{},'coverage':'complete','unmeasured':[],
            'baseline':None,'result':None,'tokenizer':None}


class PayloadMeasurer:
    def __init__(self,runtime):self.h=runtime;self.policy=runtime.cfg['accounting']

    def tokenize(self,texts):
        t=self.policy['tokenizer']
        if t['kind']=='unavailable':return [None for _ in texts]
        c=t['command']
        prohibit_git_push(c['argv'])
        try:
            p=subprocess.run(c['argv'],input=encoded({'texts':texts}).encode(),cwd=c['cwd'],env=c['environment'],
                             capture_output=True,timeout=c['timeout_seconds'])
        except (OSError,subprocess.TimeoutExpired) as exc:raise PoiseError(f'Tokenizer unavailable: {exc}') from exc
        if p.returncode or len(p.stdout)>c['max_output_bytes']:raise PoiseError('Configured tokenizer failed or exceeded output bound')
        try:result=json.loads(p.stdout)
        except (ValueError,UnicodeError) as exc:raise PoiseError('Invalid tokenizer response') from exc
        if (not isinstance(result,dict) or set(result)!={'counts'} or not isinstance(result['counts'],list)
                or len(result['counts'])!=len(texts) or any(type(n) is not int or n<0 for n in result['counts'])):
            raise PoiseError('Tokenizer must return one nonnegative count per exact input text')
        return result['counts']

    def _git_bytes(self,where,*args):
        try:r=subprocess.run(['git','-C',str(where),*args],capture_output=True,timeout=self.h.cfg['limits']['git_seconds'])
        except (OSError,subprocess.TimeoutExpired) as exc:raise PoiseError(f'Measurement Git unavailable: {exc}') from exc
        if r.returncode:raise PoiseError('Cannot read pinned Git measurement input')
        return r.stdout

    def _tree(self,where,rev):
        entries=self._git_bytes(where,'ls-tree','-r','-z',rev)
        result={}
        for line in entries.split(b'\0'):
            if not line:continue
            meta,path=line.split(b'\t',1);mode,kind,sha=meta.split()
            result[os.fsdecode(path)]=(mode.decode(),kind.decode(),sha.decode())
        return result

    def _blob(self,where,entry):
        if entry is None:return b''
        mode,kind,sha=entry
        if kind!='blob' or mode not in ('100644','100755'):raise PoiseError('Non-regular Git content')
        size=int(self._git_bytes(where,'cat-file','-s',sha))
        if size>self.policy['max_blob_bytes']:raise PoiseError('Blob exceeds explicit measurement budget')
        return self._git_bytes(where,'cat-file','blob',sha)

    def sections(self,task,definition):
        # Only current final section layers, never all iterations or report formatting.
        result={}
        with self.h.store.transaction() as db:
            for name in definition.sections:
                row=db.execute('SELECT content,content_state FROM section_layers WHERE task_id=? AND section_id=? ORDER BY submission_id DESC LIMIT 1',
                               (task['id'],name)).fetchone()
                if row is None or row['content_state']!='populated':raise PoiseError(f'Useful section is not populated: {name}')
                result[name]=row['content']
        return result

    def measure(self,task,baseline):
        definition=BenefitDefinition.parse(baseline['benefit']);cfg=baseline['policy']
        # The measured categories/tokenizer are captured at first work, not silently replaced.
        if cfg!=self.policy:raise PoiseError('Measurement policy changed since task baseline')
        result=zero_measure(self.policy['tokenizer']['kind']!='unavailable')
        result.update(baseline=baseline['git_base'],tokenizer=self.policy['tokenizer']['identity'])
        records=[];added=[];removed=[]
        # A Task without a worktree has no authored Git delta; its useful sections
        # are still measured below against the captured section baseline.
        if definition.git_categories and task['worktree'] is not None:
            revision=task['last_report']['commit']
            if not revision:raise PoiseError('Completed Git result has no exact commit')
            result['result']=revision
            worktree=Path(task['worktree'])
            before=self._tree(worktree,baseline['git_base']);after=self._tree(worktree,revision)
            paths=[p for p in sorted(before.keys()|after.keys()) if before.get(p)!=after.get(p)]
            if len(paths)>self.policy['max_files']:raise PoiseError('Diff exceeds configured file budget')
            for path in paths:
                categories=[c for c,patterns in cfg['path_categories'].items() if any(fnmatch.fnmatchcase(path,pattern) for pattern in patterns)]
                if len(categories)>1:raise PoiseError(f'Ambiguous measurement category: {path}')
                if not categories:continue # Explicit category registry defines scope; not auto-test impact.
                category=categories[0];useful=category in definition.git_categories
                try:delta=line_delta(self._blob(worktree,before.get(path)),self._blob(worktree,after.get(path)))
                except PoiseError as exc:
                    result['unmeasured'].append({'path':path,'useful':useful,'reason':str(exc)})
                    if useful:result['coverage']='partial'
                    continue
                records.append((category,useful,delta))
        contents=self.sections(task,definition)
        for name,value in contents.items():
            delta=line_delta(baseline['sections'][name].encode(),value.encode())
            result['sections'][name]={k:v for k,v in delta.items() if not k.endswith('_text')}
            records.append(('section:'+name,True,delta))
        for category,useful,d in records:
            bucket=result['categories'].setdefault(category,{'useful':useful,'added_lines':0,'removed_lines':0,'added_bytes':0,'removed_bytes':0})
            for k in ('added_lines','removed_lines','added_bytes','removed_bytes'):bucket[k]+=d[k]
            if useful:
                result['changed_lines']+=d['added_lines']+d['removed_lines'];result['changed_bytes']+=d['added_bytes']+d['removed_bytes']
                added.append(d['added_text']);removed.append(d['removed_text'])
        # Canonical instrument input: two ordered streams, not tokenization per line.
        try:
            counts=self.tokenize([''.join(added),''.join(removed)])
        except PoiseError as exc:
            counts=[None,None];result['tokenizer_error']=str(exc)
        result['changed_tokens']=None if any(n is None for n in counts) else sum(counts)
        result['payload_digest']=digest({'added':added,'removed':removed})
        result['sections_digest']=digest(contents)
        return result
