"""Tests for backup.py — turning a fetched komoot tour into a local GPX."""

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from komoot_bulk_upload.backup import backup_tour, gpx_from_tour, BackupError


def tour_with_coords(tour_id=42, date="2024-08-15T07:00:00.000Z", n=3):
    items = [{"lat": 48.0 + i / 1000, "lng": 11.0 + i / 1000, "alt": 100 + i,
              "t": i * 1000} for i in range(n)]
    return {"id": tour_id, "name": "Evening Ride", "sport": "racebike",
            "date": date, "_embedded": {"coordinates": {"items": items}}}


class GpxFromTourTests(unittest.TestCase):
    def test_builds_wellformed_gpx_with_points_and_times(self):
        data = gpx_from_tour(tour_with_coords(n=3))
        root = ET.fromstring(data)  # parses => well-formed
        ns = {"g": "http://www.topografix.com/GPX/1/1"}
        pts = root.findall(".//g:trkpt", ns)
        self.assertEqual(len(pts), 3)
        self.assertEqual(pts[0].get("lat"), "48.0")
        # first point time == tour start; third == start + 2 s
        times = [t.text for t in root.findall(".//g:trkpt/g:time", ns)]
        self.assertEqual(times[0], "2024-08-15T07:00:00Z")
        self.assertEqual(times[2], "2024-08-15T07:00:02Z")
        self.assertIn("Evening Ride", data.decode("utf-8"))

    def test_xml_special_chars_in_name_are_escaped(self):
        tour = tour_with_coords()
        tour["name"] = "Tour <A> & B"
        ET.fromstring(gpx_from_tour(tour))  # must still parse

    def test_missing_date_omits_times_but_keeps_points(self):
        tour = tour_with_coords(date=None)
        root = ET.fromstring(gpx_from_tour(tour))
        ns = {"g": "http://www.topografix.com/GPX/1/1"}
        self.assertEqual(len(root.findall(".//g:trkpt", ns)), 3)
        self.assertEqual(root.findall(".//g:trkpt/g:time", ns), [])

    def test_no_coordinates_raises(self):
        with self.assertRaises(BackupError):
            gpx_from_tour({"id": 1, "_embedded": {"coordinates": {"items": []}}})
        with self.assertRaises(BackupError):
            gpx_from_tour({"id": 1})


class FakeClient:
    def __init__(self, tour):
        self._tour = tour
        self.asked = None

    def get_tour(self, tour_id, embedded="coordinates"):
        self.asked = tour_id
        return self._tour


class BackupTourTests(unittest.TestCase):
    def test_writes_file_named_by_date_and_id(self):
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "backups")  # not yet created
            client = FakeClient(tour_with_coords(tour_id=999))
            path = backup_tour(client, 999, dest)
            self.assertEqual(client.asked, 999)
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(os.path.basename(path), "2024-08-15_999.gpx")
            ET.parse(path)  # the written file is valid XML

    def test_backup_error_propagates_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "backups")
            client = FakeClient({"id": 5, "date": "2024-01-01T00:00:00Z"})
            with self.assertRaises(BackupError):
                backup_tour(client, 5, dest)
            self.assertFalse(os.listdir(d))  # no backups dir / file created


if __name__ == "__main__":
    unittest.main()
