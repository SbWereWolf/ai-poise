"""One configured subprocess, with complete internal diagnostic streams."""
import subprocess
import tempfile

import psutil

from ..shared.protocol import MaintenanceError
from .child_subreaper import ChildSubreaper


def execute(argv: list[str], cwd: str, environment: dict[str, str],
            bound_seconds: int) -> subprocess.CompletedProcess:
    child = None
    failures = []
    stdout = stderr = b""
    runtime_errors = (OSError, subprocess.SubprocessError, psutil.Error)
    try:
        with tempfile.TemporaryFile(mode="w+b") as out, \
                tempfile.TemporaryFile(mode="w+b") as err:
            try:
                with ChildSubreaper() as ownership:
                    try:
                        child = subprocess.Popen(
                            argv, cwd=cwd, env=environment,
                            stdout=out, stderr=err, shell=False)
                        child.wait(timeout=bound_seconds)
                    except runtime_errors as error:
                        failures.append(error)
                    finally:
                        try:
                            ownership.settle(child, bound_seconds)
                        except runtime_errors as error:
                            failures.append(error)
            except runtime_errors as error:
                failures.append(error)
            captured = []
            for stream in (out, err):
                try:
                    stream.seek(0)
                    captured.append(stream.read())
                except runtime_errors as error:
                    failures.append(error)
                    captured.append(b"")
            stdout, stderr = captured
    except runtime_errors as error:
        failures.append(error)
    if failures:
        detail = "\n".join(str(error) for error in failures)
        for stream in (stdout, stderr):
            if stream:
                detail += "\n" + stream.decode(
                    "utf-8", errors="backslashreplace")
        raise MaintenanceError("process", detail) from failures[0]
    return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)


def require_success(result: subprocess.CompletedProcess) -> bytes:
    if result.returncode != 0:
        detail = (f"Command exited {result.returncode}\n"
                  + result.stdout.decode("utf-8",
                                         errors="backslashreplace")
                  + result.stderr.decode("utf-8",
                                         errors="backslashreplace"))
        raise MaintenanceError("process", detail)
    return result.stdout
