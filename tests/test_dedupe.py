"""Tests for dedupe.py — the duplicate-tour matching that powers --find-duplicates."""

import unittest
from datetime import timezone

from komoot_bulk_upload.dedupe import (
    parse_tour, find_duplicate_groups, format_groups,
    _parse_date, _distance_matches, _format_duration,
)


def raw_tour(id, date, distance=20000.0, duration=3600, sport="touringbicycle",
             name="ride", lat=48.1, lng=11.5, source=None):
    return {"id": id, "date": date, "distance": distance, "duration": duration,
            "sport": sport, "name": name,
            "start_point": {"lat": lat, "lng": lng}, "source": source}


class ParseTourTests(unittest.TestCase):
    def test_fields_are_normalized(self):
        t = parse_tour(raw_tour(7, "2024-08-15T07:23:45.000Z", distance=42000.0,
                                 duration=7200,
                                 source={"api": "de.komoot.main-api/tour/import"}))
        self.assertEqual(t.id, 7)
        self.assertEqual(t.distance_m, 42000.0)
        self.assertEqual(t.duration_s, 7200)
        self.assertEqual(t.source, "import")
        self.assertEqual(t.start.tzinfo, timezone.utc)
        self.assertIn("komoot.com/tour/7", t.url)

    def test_missing_and_bad_fields_degrade(self):
        t = parse_tour({"id": 1})
        self.assertIsNone(t.start)
        self.assertIsNone(t.distance_m)
        self.assertIsNone(t.duration_s)
        self.assertEqual(t.source, "other")
        self.assertIsNone(t.lat)

    def test_source_category_from_dict_or_string(self):
        rec = parse_tour(raw_tour(1, None,
                         source={"api": "de.komoot.main-api/tour/recorded/9"}))
        imp = parse_tour(raw_tour(2, None,
                         source='{"api":"de.komoot.main-api/tour/import"}'))
        self.assertEqual(rec.source, "recorded")
        self.assertEqual(imp.source, "import")
        self.assertEqual(parse_tour(raw_tour(3, None, source="sigma")).source,
                         "other")

    def test_import_with_recorded_type_is_not_mislabelled(self):
        # Regression: every list tour is type=tour_recorded; an import's source
        # dict carries that too, so classification must read the api path only
        # (else all tours look 'recorded' and nothing pairs -> 0 duplicates).
        imp = parse_tour(raw_tour(1, None, source={
            "api": "de.komoot.main-api/tour/import", "type": "tour_recorded"}))
        self.assertEqual(imp.source, "import")


class ParseDateTests(unittest.TestCase):
    def test_handles_z_offset_fractional_and_naive(self):
        self.assertEqual(_parse_date("2024-08-15T07:23:45.000Z").hour, 7)
        # +02:00 normalizes to 05:23 UTC.
        self.assertEqual(_parse_date("2024-08-15T07:23:45+02:00").hour, 5)
        self.assertEqual(
            _parse_date("2024-08-15T07:23:45.123456+02:00").minute, 23)
        # Naive input is assumed UTC.
        self.assertEqual(_parse_date("2024-08-15T07:23:45").tzinfo, timezone.utc)

    def test_bad_input_returns_none(self):
        for bad in ("garbage", "", None, 12345):
            self.assertIsNone(_parse_date(bad))


class DistanceMatchTests(unittest.TestCase):
    def _t(self, dist):
        return parse_tour(raw_tour(1, "2024-01-01T00:00:00Z", distance=dist))

    def test_absolute_and_relative_tolerance(self):
        # Within 1 km absolute.
        self.assertTrue(_distance_matches(self._t(42000), self._t(42800),
                                          0.20, 1000.0))
        # 10% apart -> within 20% relative.
        self.assertTrue(_distance_matches(self._t(40000), self._t(44000),
                                          0.20, 1000.0))
        # Wildly different.
        self.assertFalse(_distance_matches(self._t(15000), self._t(90000),
                                           0.20, 1000.0))

    def test_missing_distance_matches_on_time_alone(self):
        a = parse_tour(raw_tour(1, "2024-01-01T00:00:00Z", distance=None))
        b = self._t(99999)
        self.assertTrue(_distance_matches(a, b, 0.20, 1000.0))


class FindDuplicateGroupsTests(unittest.TestCase):
    def test_simple_pair_within_window(self):
        tours = [parse_tour(raw_tour(1, "2024-08-15T07:23:00Z", distance=42000)),
                 parse_tour(raw_tour(2, "2024-08-15T07:29:00Z", distance=42300))]
        groups = find_duplicate_groups(tours, time_window_s=900)
        self.assertEqual([[t.id for t in g] for g in groups], [[1, 2]])

    def test_outside_time_window_is_not_grouped(self):
        tours = [parse_tour(raw_tour(1, "2024-08-15T07:00:00Z")),
                 parse_tour(raw_tour(2, "2024-08-15T07:30:00Z"))]
        self.assertEqual(find_duplicate_groups(tours, time_window_s=900), [])

    def test_close_in_time_but_distant_is_not_grouped(self):
        tours = [parse_tour(raw_tour(1, "2024-08-15T07:00:00Z", distance=15000)),
                 parse_tour(raw_tour(2, "2024-08-15T07:05:00Z", distance=90000))]
        self.assertEqual(find_duplicate_groups(tours, time_window_s=900), [])

    def test_transitive_triple_is_one_group(self):
        tours = [
            parse_tour(raw_tour(4, "2024-09-01T06:00:00Z", distance=15000)),
            parse_tour(raw_tour(5, "2024-09-01T06:08:00Z", distance=15200)),
            parse_tour(raw_tour(6, "2024-09-01T06:14:00Z", distance=14900)),
        ]
        groups = find_duplicate_groups(tours, time_window_s=900)
        self.assertEqual(len(groups), 1)
        self.assertEqual([t.id for t in groups[0]], [4, 5, 6])

    def test_dateless_tours_are_ignored(self):
        tours = [parse_tour(raw_tour(1, None)), parse_tour(raw_tour(2, None))]
        self.assertEqual(find_duplicate_groups(tours), [])

    def test_groups_sorted_newest_first(self):
        tours = [
            parse_tour(raw_tour(1, "2024-01-01T10:00:00Z")),
            parse_tour(raw_tour(2, "2024-01-01T10:05:00Z")),
            parse_tour(raw_tour(3, "2024-06-01T10:00:00Z")),
            parse_tour(raw_tour(4, "2024-06-01T10:05:00Z")),
        ]
        groups = find_duplicate_groups(tours, time_window_s=900)
        self.assertEqual([g[0].id for g in groups], [3, 1])


# Mirror the real komoot list response: every tour is type=tour_recorded, and
# only the source api path distinguishes an app recording from a file import.
REC = {"api": "de.komoot.main-api/tour/recorded", "type": "tour_recorded"}
IMP = {"api": "de.komoot.main-api/tour/import", "type": "tour_recorded"}


class CrossSourceMatchingTests(unittest.TestCase):
    """The real-world behaviour: SIGMA import vs komoot recording of one ride."""

    def test_pairs_across_sources_hours_apart(self):
        # Import stored ~2 h after the recording, near-identical distance.
        tours = [
            parse_tour(raw_tour(1, "2024-05-01T16:33:05Z", distance=31820,
                                source=REC)),
            parse_tour(raw_tour(2, "2024-05-01T18:34:43Z", distance=31830,
                                source=IMP)),
        ]
        groups = find_duplicate_groups(tours)
        self.assertEqual([[t.id for t in g] for g in groups], [[1, 2]])

    def test_four_hour_offset_still_pairs(self):
        tours = [
            parse_tour(raw_tour(1, "2025-08-25T20:35:58Z", distance=31400,
                                source=REC)),
            parse_tour(raw_tour(2, "2025-08-26T00:36:48Z", distance=31320,
                                source=IMP)),
        ]
        self.assertEqual(len(find_duplicate_groups(tours)), 1)

    def test_same_source_is_not_a_duplicate(self):
        # Two imports the same day with near-identical distance are different
        # rides (komoot already dedupes within a source) — must not pair.
        tours = [
            parse_tour(raw_tour(1, "2023-04-15T16:15:22Z", distance=25160,
                                source=IMP)),
            parse_tour(raw_tour(2, "2023-04-15T18:58:17Z", distance=25270,
                                source=IMP)),
        ]
        self.assertEqual(find_duplicate_groups(tours), [])

    def test_distance_mismatch_blocks_cross_source_pair(self):
        tours = [
            parse_tour(raw_tour(1, "2023-10-19T14:16:52Z", distance=22440,
                                source=REC)),
            parse_tour(raw_tour(2, "2023-10-19T20:18:51Z", distance=52610,
                                source=IMP)),
        ]
        self.assertEqual(find_duplicate_groups(tours), [])

    def test_recording_pairs_with_nearer_matching_import_only(self):
        # A recorded ride, its matching import (~2 h, same distance), and an
        # unrelated import later that day (different distance) -> one pair.
        tours = [
            parse_tour(raw_tour(1, "2023-10-19T14:16:52Z", distance=22440,
                                source=REC)),
            parse_tour(raw_tour(2, "2023-10-19T16:19:53Z", distance=20980,
                                source=IMP)),
            parse_tour(raw_tour(3, "2023-10-19T20:18:51Z", distance=52610,
                                source=IMP)),
        ]
        groups = find_duplicate_groups(tours)
        self.assertEqual([[t.id for t in g] for g in groups], [[1, 2]])

    def test_beyond_window_does_not_pair(self):
        tours = [
            parse_tour(raw_tour(1, "2024-05-01T08:00:00Z", distance=30000,
                                source=REC)),
            parse_tour(raw_tour(2, "2024-05-01T18:00:00Z", distance=30000,
                                source=IMP)),
        ]
        self.assertEqual(find_duplicate_groups(tours), [])


class FormattingTests(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(_format_duration(7200), "2h00m")
        self.assertEqual(_format_duration(3660), "1h01m")
        self.assertEqual(_format_duration(0), "?")
        self.assertEqual(_format_duration(None), "?")

    def test_format_groups_includes_ids_and_links(self):
        tours = [parse_tour(raw_tour(1, "2024-08-15T07:23:00Z")),
                 parse_tour(raw_tour(2, "2024-08-15T07:29:00Z"))]
        groups = find_duplicate_groups(tours, time_window_s=900)
        text = "\n".join(format_groups(groups))
        self.assertIn("Duplicate group 1", text)
        self.assertIn("id=1", text)
        self.assertIn("komoot.com/tour/2", text)


if __name__ == "__main__":
    unittest.main()
