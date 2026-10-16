"""Late delivery gates: run after documentation, not as behavioral RED."""

from pathlib import Path
import subprocess


REPO = Path(__file__).resolve().parents[2]


def test_full_example_is_versioned_and_working_profile_ignored():
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch",
         "config/maintenance/environment-maintenance.example.json"],
        cwd=REPO, capture_output=True, text=True)
    assert tracked.returncode == 0, tracked.stderr
    ignored = subprocess.run(
        ["git", "check-ignore", "operator/profile/catalog.json"],
        cwd=REPO, capture_output=True, text=True)
    assert ignored.returncode == 0, ignored.stderr
