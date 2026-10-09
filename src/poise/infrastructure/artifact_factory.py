"""Confined, non-overwriting file publication. No task lifecycle writes."""
from dataclasses import dataclass
from pathlib import Path
import os
import hashlib
import stat
import tempfile
import io
from ..modules.artifact_factory.domain import ArtifactPlan, FileArtifactSource, RegisteredArtifactMaterial, relative
from ..artifacts import artifact_identity
from ..common import PoiseError
from .locking import exclusive_lock
from .file_publication import BinaryFilePublisher


@dataclass(frozen=True)
class PreparedFile:
    scope: str
    relative: str
    path: Path
    content: bytes
    source: FileArtifactSource | None = None


class FileArtifactFactory:
    def __init__(self, roots, config, lock, wait, poll):
        self.roots=dict(roots)
        self.config=config
        self.lock,self.wait,self.poll=lock,wait,poll

    def _observe_destination(self, scope, name, *, directory):
        if scope not in self.roots:
            raise PoiseError(f'No current {scope} owner for artifact')
        root = Path(self.roots[scope])
        self._without_links(root, 'Artifact destination owner root')
        if root.exists() and not root.is_dir():
            raise PoiseError('Artifact owner root is not a directory')
        artifact_root = root / self.config['artifact_directories'][scope]
        full = root / relative(name)
        if not full.is_relative_to(artifact_root):
            raise PoiseError('Artifact directory must be inside configured permanent artifacts')
        self._without_links(full, 'Artifact destination')
        if not full.resolve().is_relative_to(root.resolve()):
            raise PoiseError('Artifact destination leaves its owner')
        if full.exists() and not (full.is_dir() if directory else full.is_file()):
            kind = 'directory' if directory else 'regular file'
            raise PoiseError(f'Artifact destination is not a {kind}')
        return full

    def _destination(self, scope, name):
        if scope not in self.roots:
            raise PoiseError(f'No current {scope} owner for artifact')
        prefix = self.config['artifact_directories'][scope]
        return self._observe_destination(scope, f'{prefix}/{name}', directory=False)

    def observe_directory(self, scope, owner_relative_path):
        """Observe an explicit owner-relative directory without creating it."""
        return self._observe_destination(scope, owner_relative_path, directory=True)

    def prepare(self, items):
        plan=ArtifactPlan.parse(items,self.config)
        prepared=[]
        for item in plan.items:
            path=self._destination(item.scope,item.path)
            source = item.content if isinstance(item.content, FileArtifactSource) else None
            if source is None:
                content=item.content.encode('utf-8')
            else:
                content=self._source_bytes(source)
            if path.exists() and self._read_registered(path, 'Artifact destination') != hashlib.sha256(content).hexdigest():
                raise PoiseError(f'Artifact already exists with different content: {path}; use a new path')
            prepared.append(PreparedFile(item.scope,item.path,path,content,source))
        return tuple(prepared)

    def _source_bytes(self, source):
        path = Path(source.path)
        try:
            self._without_links(path, 'Artifact source file')
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise PoiseError(f'Artifact source is not a regular file: {path}')
            if info.st_size > self.config['max_artifact_bytes']:
                raise PoiseError(f'Artifact source exceeds max_artifact_bytes: {path}')
            output = io.BytesIO()
            if self._read_registered(path, 'Artifact source file', output,
                                     max_bytes=self.config['max_artifact_bytes']) != source.digest:
                raise PoiseError(f'Artifact source digest mismatch: {path}')
            return output.getvalue()
        except OSError as exc:
            raise PoiseError(f'Artifact source file missing or unreadable: {path}: {exc}') from exc

    def materialize(self, prepared):
        with exclusive_lock(self.lock,self.wait,self.poll):
            # Validate the entire batch again before publishing any member.
            for item in prepared:
                if item.source is not None and self._source_bytes(item.source) != item.content:
                    raise PoiseError(f'Artifact source changed after preparation: {item.source.path}')
                if self._destination(item.scope,item.relative)!=item.path:
                    raise PoiseError('Artifact destination changed')
                if item.path.exists() and self._read_registered(item.path, 'Artifact destination') != hashlib.sha256(item.content).hexdigest():
                    raise PoiseError('Artifact changed after preparation')
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
                raise PoiseError(f'Artifact publication incomplete; preserved paths={completed}; retry identical batch: {exc}') from exc
            return completed


    _without_links = staticmethod(BinaryFilePublisher.without_links)
    _read_registered = staticmethod(BinaryFilePublisher.read)

    def _recovery_destination(self, scope, name):
        # Registry-relative names include handoffs/, artifacts/, etc.; unlike
        # new authored text, they must not receive an artifact-directory prefix.
        root = Path(self.roots[scope])
        target = root / relative(name)
        self._without_links(root, 'Artifact destination owner root')
        self._without_links(target, 'Artifact destination')
        if not target.is_relative_to(root):
            raise PoiseError('Artifact destination leaves its owner')
        candidates = [(len(Path(r).parts), key) for key, r in self.roots.items()
                      if target.is_relative_to(Path(r))]
        depth = max(size for size, _ in candidates)
        if [key for size, key in candidates if size == depth] != [scope]:
            raise PoiseError('Artifact destination scope/owner identity changed')
        return target

    def _registered_plan(self, records, source_roots, owners, materials=None):
        scopes = {row['scope'] for row in records}
        if set(source_roots) != scopes:
            raise PoiseError('Explicit source owner roots must match selected artifact scopes')
        planned = []
        for row in records:
            scope = row['scope']
            if scope not in self.roots or row['owner'] != owners.get(scope):
                raise PoiseError('Registered artifact owner identity does not match')
            old_root = Path(source_roots[scope])
            source = Path(row['path'])
            if str(source) != row['path'] or not source.is_absolute():
                raise PoiseError('Registered source path is not canonical')
            try:
                name = relative(source.relative_to(old_root).as_posix())
            except ValueError as exc:
                raise PoiseError('Registered source is outside explicit owner root') from exc
            if artifact_identity(scope, row['owner'], name) != row['id']:
                raise PoiseError('Registered relative path/owner identity does not match source root')
            self._without_links(old_root, 'Artifact source owner root')
            if materials is not None:
                if set(materials)!=set(r['id'] for r in records):
                    raise PoiseError('Explicit material identities do not match artifact set')
                material = materials[row['id']]
                if not isinstance(material, RegisteredArtifactMaterial) or not material.matches(row):
                    raise PoiseError('Explicit physical material changed registered identity')
                source = Path(material.physical_path)
                if not source.is_absolute():
                    raise PoiseError('Explicit physical material path must be absolute')
            target = self._recovery_destination(scope, name)
            if self._read_registered(source, 'Artifact source file') != row['digest']:
                raise PoiseError(f'Artifact source digest mismatch: {source}')
            if target.exists() and self._read_registered(target, 'Artifact destination') != row['digest']:
                raise PoiseError(f'Artifact destination has different content: {target}')
            planned.append((row, source, name, target))
        return planned

    def plan_registered_copy(self, records, source_roots, owners, destination_owners):
        return self.plan_material_copy(records, source_roots, owners, destination_owners,
            materials={r['id']: RegisteredArtifactMaterial.active(r) for r in records})

    def plan_material_copy(self, records, source_roots, owners, destination_owners, *, materials):
        """Observe the complete delivery without publishing or changing ownership."""
        try:
            plan = self._registered_plan(records, source_roots, owners, materials)
            return [self._copy_record(row, source, name, target, destination_owners)
                    for row, source, name, target in plan]
        except OSError as exc:
            raise PoiseError(f'Artifact copy preflight failed: {exc}') from exc

    @staticmethod
    def _copy_record(row, source, name, target, owners):
        scope = row['scope']
        if scope not in owners:
            raise PoiseError('Artifact copy has no destination owner')
        return {**row, 'id': artifact_identity(scope, owners[scope], name),
                'owner': owners[scope], 'path': str(target), 'relative_path': name,
                'source_id': row['id'], 'source_path': row['path']}

    def copy_registered(self, records, source_roots, owners, destination_owners):
        return self.copy_materials(records, source_roots, owners, destination_owners,
            materials={r['id']: RegisteredArtifactMaterial.active(r) for r in records})

    def copy_materials(self, records, source_roots, owners, destination_owners, *, materials):
        """Deliver new owner-specific references; never rebind source references."""
        return self._publish_registered(records, source_roots, owners, destination_owners, materials=materials)

    def recover_registered(self, records, source_roots, owners):
        """Restore the same identities; registry rebind belongs to the caller."""
        return self._publish_registered(records, source_roots, owners)

    def _publish_registered(self, records, source_roots, owners, destination_owners=None, *, materials=None):
        """One non-overwriting binary publication path for recovery and delivery."""
        completed = []
        try:
            with exclusive_lock(self.lock, self.wait, self.poll):
                planned = self._registered_plan(records, source_roots, owners, materials)
                if destination_owners is not None:
                    # Validate all owner mappings before the first file effect.
                    for row, source, name, target in planned:
                        self._copy_record(row, source, name, target, destination_owners)
                # The complete batch was checked before the first publication.
                for row, source, name, target in planned:
                    self._recovery_destination(row['scope'], name)
                    BinaryFilePublisher.publish(source, target, row['digest'], self.config['file_mode'])
                    completed.append(
                        {**row, 'path': str(target), 'source_path': str(source), 'relative_path': name}
                        if destination_owners is None else
                        self._copy_record(row, source, name, target, destination_owners))
                # Ordinary validation still checks content on future use; this
                # validates the exact publication set immediately before rebind.
                for row in completed:
                    path = self._recovery_destination(row['scope'], row['relative_path'])
                    if self._read_registered(path, 'Artifact destination') != row['digest']:
                        raise PoiseError(f'Artifact destination digest changed: {path}')
        except OSError as exc:
            raise PoiseError(f'Artifact recovery file publication incomplete; preserved={completed}; '
                             f'retry identical request: {exc}') from exc
        return completed
