"""Tests for payload.py — what bytes/data_type each file format yields."""

import os
import tempfile
import unittest

from komoot_bulk_upload.payload import upload_payload, UnsupportedFormat


class UploadPayloadTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _write(self, name, data=b"<xml/>"):
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_gpx_passthrough(self):
        data, dtype = upload_payload(self._write("ride.gpx", b"<gpx>1</gpx>"))
        self.assertEqual(data, b"<gpx>1</gpx>")
        self.assertEqual(dtype, "gpx")

    def test_tcx_passthrough_and_extension_case(self):
        data, dtype = upload_payload(self._write("ride.TCX", b"<TrainingCenter/>"))
        self.assertEqual(data, b"<TrainingCenter/>")
        self.assertEqual(dtype, "tcx")

    def test_fit_is_gated(self):
        with self.assertRaises(UnsupportedFormat):
            upload_payload(self._write("ride.fit", b"\x0e\x10"))

    def test_unknown_extension_rejected(self):
        with self.assertRaises(UnsupportedFormat):
            upload_payload(self._write("ride.kml"))


if __name__ == "__main__":
    unittest.main()
