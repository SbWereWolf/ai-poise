"""Confined, non-overwriting file publication. No task lifecycle writes."""
from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
from ..modules.artifact_factory.domain import ArtifactPlan
from ..common import HarnessError
from .locking import exclusive_lock


@dataclass(frozen=True)
class PreparedFile:
    scope: str
    relative: str
    path: Path
    content: bytes


class FileArtifactFactory:
    def __init__(self, roots, config, lock, wait, poll):
        self.roots=dict(roots)
        self.config=config
        self.lock,self.wait,self.poll=lock,wait,poll

    def _destination(self, scope, relative):
        if scope not in self.roots:
            raise HarnessError(f'No current {scope} owner for artifact')
        root=Path(self.roots[scope])
        if root.is_symlink():
            raise HarnessError('Artifact owner root is a symlink')
        full=root/self.config['artifact_directories'][scope]/relative
        # Refuse links rather than follow even an in-root alias. It keeps repeat
        # identity and destination stable across publication/recovery.
        cursor=root
        for part in full.relative_to(root).parts:
            cursor=cursor/part
            if cursor.is_symlink():
                raise HarnessError('Artifact destination contains a symlink')
            if cursor.exists() and cursor!=full and not cursor.is_dir():
                raise HarnessError('Artifact parent is not a directory')
        if not full.resolve().is_relative_to(root.resolve()):
            raise HarnessError('Artifact destination leaves its owner')
        if full.exists() and not full.is_file():
            raise HarnessError('Artifact destination is not a regular file')
        return full

    def prepare(self, items):
        plan=ArtifactPlan.parse(items,self.config)
        prepared=[]
        for item in plan.items:
            path=self._destination(item.scope,item.path)
            content=item.content.encode('utf-8')
            if path.exists() and path.read_bytes()!=content:
                raise HarnessError(f'Artifact already exists with different content: {path}; use a new path')
            prepared.append(PreparedFile(item.scope,item.path,path,content))
        return tuple(prepared)

    def materialize(self, prepared):
        with exclusive_lock(self.lock,self.wait,self.poll):
            # Validate the entire batch again before publishing any member.
            for item in prepared:
                if self._destination(item.scope,item.relative)!=item.path:
                    raise HarnessError('Artifact destination changed')
                if item.path.exists() and item.path.read_bytes()!=item.content:
                    raise HarnessError('Artifact changed after preparation')
            completed=[]
            try:
                for item in prepared:
                    if not item.path.exists():
                        item.path.parent.mkdir(parents=True,exist_ok=True)
                        fd,name=tempfile.mkstemp(dir=item.path.parent)
                        try:
                            with os.fdopen(fd,'wb') as stream:
                                os.fchmod(stream.fileno(),self.config['file_mode'])
                                stream.write(item.content);stream.flush();os.fsync(stream.fileno())
                            self._destination(item.scope,item.relative)
                            # Hard-link publication never overwrites an existing file.
                            os.link(name,item.path)
                        finally:
                            Path(name).unlink(missing_ok=True)
                    completed.append(str(item.path))
            except OSError as exc:
                raise HarnessError(f'Artifact publication incomplete; preserved paths={completed}; retry identical batch: {exc}') from exc
            return completed
