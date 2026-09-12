from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile


PREFIX = "ai-poise-task-cleanup-"
MARKER = ".ai-poise-owned-basetemp"
_WATCHDOG = r"""
import os
from pathlib import Path
import shutil
import sys
import time

parent = int(sys.argv[1])
root = Path(sys.argv[2]).resolve()
owner = Path(sys.argv[3])
token = sys.argv[4]
while True:
    try:
        os.kill(parent, 0)
    except ProcessLookupError:
        break
    except PermissionError:
        pass
    time.sleep(0.1)
try:
    if (owner.parent.resolve() == root and owner.name.startswith("ai-poise-task-cleanup-")
            and (owner / ".ai-poise-owned-basetemp").read_text(encoding="utf-8") == token):
        shutil.rmtree(owner)
except FileNotFoundError:
    pass
"""


@dataclass(frozen=True)
class OwnedBasetemp:
    root: Path
    owner: Path
    path: Path
    token: str

    @classmethod
    def create(cls, root: Path):
        root = root.resolve(strict=True)
        token = secrets.token_hex(32)
        owner = Path(tempfile.mkdtemp(prefix=PREFIX, dir=root))
        path = owner / "pytest"
        path.mkdir()
        (owner / MARKER).write_text(token, encoding="utf-8")
        subprocess.Popen(
            [sys.executable, "-c", _WATCHDOG, str(os.getpid()), str(root), str(owner), token],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        return cls(root, owner, path, token)

    def remove(self, token=None):
        if not self.owner.exists():
            return
        received = self.token if token is None else token
        if (self.owner.parent.resolve() != self.root
                or not self.owner.name.startswith(PREFIX)
                or (self.owner / MARKER).read_text(encoding="utf-8") != received):
            raise RuntimeError("Refusing to remove an unverified pytest basetemp")
        shutil.rmtree(self.owner)
