"""Complete Git receipts; display budgets never enter machine transport."""
import locale
import subprocess
import sys

from ..common import PoiseError, prohibit_git_push


def run_git_receipt(cwd, arguments, timeout_seconds, environment):
    prohibit_git_push(['git', *arguments])
    argv = ['git', '-C', str(cwd), *arguments]
    try:
        result = subprocess.run(argv, capture_output=True, timeout=timeout_seconds,
                                env=environment)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PoiseError(f'Git {arguments[0]} did not complete: {exc}') from exc
    encoding = 'utf-8' if sys.flags.utf8_mode else locale.getencoding()
    return {'argv': argv, 'actual_exit_code': result.returncode,
            'stdout': result.stdout.decode(encoding, errors='strict'),
            'stderr': result.stderr.decode(encoding, errors='strict')}
