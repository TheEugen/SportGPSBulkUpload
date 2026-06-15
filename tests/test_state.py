"""Tests for state.py — the resumable, content-hashed upload state."""

import hashlib
import os
import tempfile
import unittest

from komoot_bulk_upload.state import UploadState, file_hash, activity_key


class ActivityKeyTests(unittest.TestCase):
    def test_lowercased_stem_regardless_of_extension(self):
        self.assertEqual(activity_key("/a/b/2024_05_01__18_34.GPX"),
                         "2024_05_01__18_34")
        self.assertEqual(activity_key("Ride.tcx"), "ride")
        # Same activity in two formats yields the same key (cross-format dedupe).
        self.assertEqual(activity_key("x/Ride.gpx"), activity_key("y/ride.tcx"))


class FileHashTests(unittest.TestCase):
    def test_matches_sha1_of_contents(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.bin")
            with open(p, "wb") as f:
                f.write(b"hello world")
            self.assertEqual(file_hash(p),
                             hashlib.sha1(b"hello world").hexdigest())


class UploadStateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "state.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_record_persists_and_is_done(self):
        s = UploadState(self.path)
        self.assertFalse(s.is_done("abc"))
        s.record("abc", status="created", file="r.gpx", activity_key="r")
        self.assertTrue(s.is_done("abc"))
        # Reload from disk: state survives a fresh run.
        self.assertTrue(UploadState(self.path).is_done("abc"))

    def test_failed_status_is_not_done(self):
        s = UploadState(self.path)
        s.record("abc", status="failed", file="r.gpx")
        self.assertFalse(s.is_done("abc"))

    def test_done_activity_keys_includes_duplicates(self):
        s = UploadState(self.path)
        s.record("h1", status="created", file="a.gpx", activity_key="a")
        s.record("h2", status="duplicate", file="b.gpx", activity_key="b")
        s.record("h3", status="failed", file="c.gpx", activity_key="c")
        self.assertEqual(s.done_activity_keys(), {"a", "b"})

    def test_done_keys_derived_from_path_for_legacy_records(self):
        # Older records had no activity_key; derive it from the stored path.
        s = UploadState(self.path)
        s.record("h1", status="created", file="/x/2024_05_01__18_34.gpx")
        self.assertEqual(s.done_activity_keys(), {"2024_05_01__18_34"})

    def test_corrupt_state_file_starts_fresh(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        s = UploadState(self.path)
        self.assertEqual(s.done_activity_keys(), set())


if __name__ == "__main__":
    unittest.main()
