"""Tests for gpx.py — title + elapsed-time extraction from GPX/TCX."""

import os
import tempfile
import unittest

from komoot_bulk_upload.gpx import read_metadata

GPX = """<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>Evening Ride</name></metadata>
  <trk><trkseg>
    <trkpt lat="48.1" lon="11.5"><time>2024-08-15T07:00:00Z</time></trkpt>
    <trkpt lat="48.2" lon="11.6"><time>2024-08-15T08:00:00Z</time></trkpt>
  </trkseg></trk>
</gpx>
"""

# SIGMA TCX: <Name> holds the device, not a tour title — so naming is skipped.
TCX = """<?xml version="1.0"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities><Activity Sport="Biking">
    <Id>2024-08-15T07:00:00Z</Id>
    <Lap><Track>
      <Trackpoint><Time>2024-08-15T07:00:00Z</Time></Trackpoint>
      <Trackpoint><Time>2024-08-15T07:30:00Z</Time></Trackpoint>
    </Track></Lap>
    <Creator><Name>ROX GPS 11.0</Name></Creator>
  </Activity></Activities>
</TrainingCenterDatabase>
"""


class ReadMetadataTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _write(self, name, text):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_gpx_name_and_elapsed(self):
        name, elapsed = read_metadata(self._write("ride.gpx", GPX))
        self.assertEqual(name, "Evening Ride")
        self.assertEqual(elapsed, 3600)

    def test_tcx_skips_device_name_but_reads_elapsed(self):
        name, elapsed = read_metadata(self._write("ride.tcx", TCX))
        self.assertIsNone(name)  # not "ROX GPS 11.0"
        self.assertEqual(elapsed, 1800)

    def test_fit_and_unknown_degrade_to_none(self):
        self.assertEqual(read_metadata(self._write("ride.fit", "binary")),
                         (None, None))
        self.assertEqual(read_metadata(self._write("ride.kml", "<kml/>")),
                         (None, None))

    def test_malformed_xml_does_not_raise(self):
        name, elapsed = read_metadata(self._write("bad.gpx", "<gpx><trk>"))
        self.assertIsNone(name)
        self.assertIsNone(elapsed)


if __name__ == "__main__":
    unittest.main()
