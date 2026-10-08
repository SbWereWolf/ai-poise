"""Bounded test-owned proof-byte observation; never alters legitimate audit rows."""
import base64
import io
import hashlib
import json
import zipfile
import zlib
from pathlib import Path


def contains_proof(value, markers):
    data = value.encode() if isinstance(value, str) else value
    if not isinstance(data, bytes):
        return False
    representations = []
    for marker in markers:
        representations.extend((marker, json.dumps(marker.decode(errors="replace"))[1:-1].encode(),
                                base64.b64encode(marker), marker.hex().encode()))
    if any(marker in data for marker in representations):
        return True
    # Bounded known byte containers, not a claim of arbitrary-code decoding.
    if len(data) <= 1024 * 1024:
        try:
            decoded = zlib.decompressobj(47).decompress(data, 1024 * 1024)
        except zlib.error:
            decoded = b""
        if any(marker in decoded for marker in markers):
            return True
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                assert len(entries) <= 100 and sum(x.file_size for x in entries) <= 1024 * 1024
                return any(any(marker in archive.read(item) for marker in markers) for item in entries)
    return False


def proof_cells(tools, markers):
    """Observe every explicit column of this bounded isolated test store.

    Preserve existing metadata/preview/history cells. Retirement cannot introduce
    new proof-bearing cells (including a new table) as an undisclosed archive.
    """
    locations = {}
    with tools.runtime.store.transaction() as database:
        tables = [row[0] for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE ? ORDER BY name",
            ("table", "sqlite_%"))]
        assert len(tables) <= 200
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = [row[1] for row in database.execute(f"PRAGMA table_info({quoted})")]
            names = [f'"{name.replace(chr(34), chr(34) * 2)}"' for name in columns]
            assert database.execute(f"SELECT COUNT(1) FROM {quoted}").fetchone()[0] <= 2000
            rows = database.execute(f"SELECT rowid,{','.join(names)} FROM {quoted} ORDER BY rowid")
            for row in rows:
                for column, cell in zip(columns, row[1:]):
                    if contains_proof(cell, markers):
                        data = cell.encode() if isinstance(cell, str) else cell
                        representations = []
                        for marker in markers:
                            representations.extend((marker, json.dumps(marker.decode(errors="replace"))[1:-1].encode(),
                                                    base64.b64encode(marker), marker.hex().encode()))
                        weight = sum(data.count(marker) for marker in representations)
                        locations[(table, row[0], column)] = max(1, weight)
    return locations


def no_new_archive(tools, before, markers):
    after = proof_cells(tools, markers)
    assert all(key in before and weight <= before[key] for key, weight in after.items()), "New SQLite proof-byte archive"
    database = tools.runtime.store.database.path
    native_files = {database, Path(str(database) + "-wal"), Path(str(database) + "-shm")}

    for path in tools.runtime.state.rglob("*"):
        if path.is_file() and path not in native_files and path.suffix != ".lock":
            assert not contains_proof(path.read_bytes(), markers), str(path)


def material_provenance(tools, markers):
    """Bounded exact-file plus logical-cell provenance for this isolated store.

    Only the actual live SQLite database/WAL/SHM use logical cell observation.
    Snapshot databases, lock-named files and byte archives are ordinary files;
    no directory, extension or archive family is exempted.
    """
    database = tools.runtime.store.database.path
    native = {database, Path(str(database) + "-wal"), Path(str(database) + "-shm")}
    paths = list(tools.runtime.state.rglob("*"))
    assert len(paths) <= 2000, "Isolated provenance observation exceeds declared bound"
    files = {}
    for path in paths:
        if path.is_file() and path not in native:
            data = path.read_bytes()
            assert len(data) <= 4 * 1024 * 1024, "Isolated file exceeds declared observation bound"
            if contains_proof(data, markers):
                files[str(path)] = hashlib.sha256(data).hexdigest()
    return {"cells": proof_cells(tools, markers), "files": files}


def no_new_material_archive(tools, before, markers):
    """Permit unchanged exact live inputs/package and existing historical cells."""
    after = material_provenance(tools, markers)
    assert all(key in before["cells"] and weight <= before["cells"][key]
               for key, weight in after["cells"].items()), "New SQLite proof-byte archive"
    assert all(before["files"].get(path) == digest for path, digest in after["files"].items()), \
        "New filesystem proof-byte archive"
