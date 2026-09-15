"""Read only declared files of the inspected AI-poise copy; no index/config writes."""
import hashlib
from pathlib import Path

from ..common import PoiseError, descendant
from .test_packages import AiPoiseTestPackages


class NavigationFiles:
    def checkout(self, path):
        return str(AiPoiseTestPackages._assert_ai_poise_checkout(Path(path)))

    def snapshot(self, checkout, files):
        result = {}
        for name in sorted(files):
            if Path(name).is_absolute() or '\\' in name or '..' in Path(name).parts:
                raise PoiseError('Navigation file must be relative to the inspected checkout')
            path = descendant(Path(checkout), name)
            try:
                if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                    raise PoiseError(f'Navigation proof requires a regular file <=4 MiB: {name}')
                result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                raise PoiseError(f'Cannot read navigation proof file {name}: {exc}') from exc
        return result
