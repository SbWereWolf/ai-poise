"""The shipped reference set has one current development source."""
from pathlib import Path
import unittest


class RetiredDevelopmentSource(unittest.TestCase):
    def test_obsolete_reference_is_absent(self):
        repository = Path(__file__).resolve().parents[2]
        self.assertFalse(
            (repository / 'config/workflows/development.json').exists(),
            'The obsolete development reference is still shipped.',
        )
