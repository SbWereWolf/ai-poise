"""Absent command detection must preserve arbitrary failures as failures."""
import json

import pytest

from tests.content_reader.helpers import ContentReadUnavailable, FIXTURES, decoded


def test_missing_reader_entry_is_distinguished_from_arbitrary_failure():
    absent = (FIXTURES / 'entry-absent.stderr.txt').read_text()
    with pytest.raises(ContentReadUnavailable):
        decoded(2, '', absent)
    # Same words inside an ordinary command body are not an absent entry.
    assert decoded(2, '{"reason":"invalid policy"}', absent) == (2, {'reason': 'invalid policy'})
    for code, stdout, stderr in [(1, '', absent), (2, '', 'ModuleNotFoundError: poise'),
                                  (2, '', 'usage: content-read invalid configuration')]:
        with pytest.raises(json.JSONDecodeError):
            decoded(code, stdout, stderr)
    assert decoded(0, '{"status":"ready"}', '') == (0, {'status': 'ready'})
