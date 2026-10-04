"""Historical passing check with explicitly test-owned observable inputs/effects."""

import os
from pathlib import Path
import subprocess
import unittest

from src.double import double


class Regression(unittest.TestCase):
    def test_double(self):
        self.assertIn(double(2), (3, 4))
        self.assertFalse(Path(os.environ["REPLAY_FAIL_FILE"]).exists())
        with Path(os.environ["REPLAY_LOG"]).open("a") as stream:
            stream.write(subprocess.check_output(["git", "rev-parse", "HEAD"], text=True))
