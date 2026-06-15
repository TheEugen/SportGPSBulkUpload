"""Tests for cli.py helpers — file collection, credentials, delete selection."""

import contextlib
import io
import os
import tempfile
import unittest
from types import SimpleNamespace

from komoot_bulk_upload.cli import (
    collect_files, resolve_credentials, _parse_delete_selection,
)


class CollectFilesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for name in ("a.gpx", "b.GPX", "c.tcx", "d.fit", "notes.txt", "e.kml"):
            with open(os.path.join(self.dir.name, name), "w") as f:
                f.write("x")

    def test_directory_collects_supported_extensions_only(self):
        found = {os.path.basename(p) for p in collect_files([self.dir.name])}
        self.assertEqual(found, {"a.gpx", "b.GPX", "c.tcx", "d.fit"})

    def test_results_are_absolute_and_deduplicated(self):
        one = os.path.join(self.dir.name, "a.gpx")
        files = collect_files([self.dir.name, one, one])
        self.assertTrue(all(os.path.isabs(f) for f in files))
        self.assertEqual(len(files), len(set(files)))

    def test_explicit_unsupported_file_is_dropped(self):
        txt = os.path.join(self.dir.name, "notes.txt")
        self.assertEqual(collect_files([txt]), [])


class ResolveCredentialsTests(unittest.TestCase):
    def test_token_takes_precedence(self):
        args = SimpleNamespace(email="me@example.com", password="pw", token="tok")
        self.assertEqual(resolve_credentials(args),
                         ("me@example.com", None, "tok"))

    def test_email_and_password_passed_through(self):
        args = SimpleNamespace(email="me@example.com", password="pw", token=None)
        self.assertEqual(resolve_credentials(args),
                         ("me@example.com", "pw", None))


class ParseDeleteSelectionTests(unittest.TestCase):
    def setUp(self):
        self.group = [SimpleNamespace(id=101), SimpleNamespace(id=102),
                      SimpleNamespace(id=103)]

    def test_valid_indices_map_to_ids(self):
        self.assertEqual(_parse_delete_selection("1,3", self.group), [101, 103])

    def test_out_of_range_and_junk_are_ignored(self):
        # The parser warns about bad tokens on stdout; suppress it here.
        with contextlib.redirect_stdout(io.StringIO()):
            result = _parse_delete_selection("5,x,1", self.group)
        self.assertEqual(result, [101])

    def test_blank_selects_nothing(self):
        self.assertEqual(_parse_delete_selection("", self.group), [])


if __name__ == "__main__":
    unittest.main()
