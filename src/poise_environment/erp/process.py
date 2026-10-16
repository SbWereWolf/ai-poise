"""One configured subprocess, with complete internal diagnostic streams."""
import subprocess

from ..shared.protocol import MaintenanceError


def execute(argv: list[str], cwd: str, environment: dict[str, str],
            bound_seconds: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, cwd=cwd, env=environment,
                              capture_output=True,
                              timeout=bound_seconds, check=False,
                              shell=False)
    except subprocess.TimeoutExpired as error:
        detail = str(error)
        for stream in (error.stdout, error.stderr):
            if stream:
                detail += "\n" + stream.decode("utf-8", errors="backslashreplace")
        raise MaintenanceError("process", detail) from error
    except OSError as error:
        raise MaintenanceError("process", str(error)) from error


def require_success(result: subprocess.CompletedProcess) -> bytes:
    if result.returncode != 0:
        detail = (f"Command exited {result.returncode}\n"
                  + result.stdout.decode("utf-8",
                                         errors="backslashreplace")
                  + result.stderr.decode("utf-8",
                                         errors="backslashreplace"))
        raise MaintenanceError("process", detail)
    return result.stdout
