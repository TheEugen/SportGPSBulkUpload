"""Find likely-duplicate tours in a komoot account.

The problem this solves: an activity tracked on two devices at once (a SIGMA ROX
and the komoot app) ends up as two tours komoot does NOT auto-dedupe, because the
two devices are stopped a few metres apart, so their start/end coordinates differ.

Coordinates are therefore the wrong signal. The robust one is **start time**: two
recordings of the same ride begin within a couple of minutes of each other and
cover a similar distance, regardless of GPS jitter. This module groups tours on
that basis and never deletes anything — it only reports candidates for the user
to review and remove by hand.

Stdlib only; takes the raw tour dicts from ``KomootClient.list_tours()``.
"""

import re
from datetime import datetime, timezone

from .api import TOUR_WEB_URL


class Tour:
    """A normalized view of a komoot tour, with only the fields dedupe needs."""

    def __init__(self, id, name, sport, start, distance_m, duration_s,
                 lat, lng, source):
        self.id = id
        self.name = name
        self.sport = sport
        self.start = start            # tz-aware datetime (UTC) or None
        self.distance_m = distance_m  # float metres or None
        self.duration_s = duration_s  # int seconds or None
        self.lat = lat
        self.lng = lng
        self.source = source          # short origin hint (e.g. "import") or None

    @property
    def url(self):
        return TOUR_WEB_URL.format(tour_id=self.id)

    def summary(self):
        when = self.start.astimezone().strftime("%Y-%m-%d %H:%M") if self.start \
            else "????-??-?? ??:??"
        dist = "{:6.1f} km".format(self.distance_m / 1000.0) \
            if self.distance_m is not None else "   ?? km"
        dur = _format_duration(self.duration_s)
        src = "  [{}]".format(self.source) if self.source else ""
        return "{}  {}  {:>7}  {:<16} id={}  {!r}{}\n        {}".format(
            when, dist, dur, self.sport or "?", self.id, self.name or "", src,
            self.url)


def parse_tour(raw):
    """Build a :class:`Tour` from one raw komoot tour dict (lenient about fields)."""
    start = _parse_date(raw.get("date"))
    point = raw.get("start_point") or {}
    return Tour(
        id=raw.get("id"),
        name=raw.get("name"),
        sport=raw.get("sport"),
        start=start,
        distance_m=_to_float(raw.get("distance")),
        duration_s=_to_int(raw.get("duration")),
        lat=_to_float(point.get("lat")),
        lng=_to_float(point.get("lng")),
        source=_source_hint(raw),
    )


def find_duplicate_groups(tours, time_window_s=900, distance_tol=0.20,
                          distance_abs_m=1000.0):
    """Group tours that look like the same ride recorded more than once.

    Two tours match when their start times are within ``time_window_s`` AND their
    distances are close — within ``distance_tol`` relative (e.g. 0.20 = 20%) OR
    ``distance_abs_m`` absolute. (If either distance is missing, the time window
    alone decides, so nothing is silently dropped.) Matching is transitive, so
    three recordings of one ride form a single group.

    Returns a list of groups (each a list of >= 2 :class:`Tour`), newest first;
    tours within a group are ordered by start time.
    """
    items = [t for t in tours if t.start is not None]
    items.sort(key=lambda t: t.start)

    # Union-find over the time-sorted list: only nearby starts can ever match,
    # so the inner loop breaks as soon as the gap exceeds the window.
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        parent[find(i)] = find(j)

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            gap = (items[j].start - items[i].start).total_seconds()
            if gap > time_window_s:
                break
            if _distance_matches(items[i], items[j], distance_tol, distance_abs_m):
                union(i, j)

    groups = {}
    for idx in range(len(items)):
        groups.setdefault(find(idx), []).append(items[idx])

    result = [g for g in groups.values() if len(g) >= 2]
    for g in result:
        g.sort(key=lambda t: t.start)
    result.sort(key=lambda g: g[0].start, reverse=True)
    return result


def format_groups(groups):
    """Render duplicate groups as a list of printable lines (no trailing print)."""
    lines = []
    for n, group in enumerate(groups, 1):
        lines.append("Duplicate group {} — {} tours:".format(n, len(group)))
        for tour in group:
            lines.append("    " + tour.summary())
        lines.append("")
    return lines


# --- internals -----------------------------------------------------------

def _distance_matches(a, b, rel, abs_m):
    if a.distance_m is None or b.distance_m is None:
        return True  # can't compare distance; let the time window decide
    diff = abs(a.distance_m - b.distance_m)
    if diff <= abs_m:
        return True
    return diff / max(a.distance_m, b.distance_m, 1.0) <= rel


def _format_duration(seconds):
    if not seconds:
        return "?"
    seconds = int(seconds)
    return "{}h{:02d}m".format(seconds // 3600, (seconds % 3600) // 60)


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _source_hint(raw):
    """A short origin label for the report, if komoot exposes one.

    Imported (uploaded) tours and app-recorded tours carry different origin
    metadata; this surfaces whatever is present so the user can tell which is
    the SIGMA upload and which the komoot recording when deciding what to keep.
    """
    source = raw.get("source")
    if isinstance(source, dict):
        source = source.get("type") or source.get("name") or source.get("api")
    if isinstance(source, str) and source.strip():
        return source.strip()[:40]
    return None


def _parse_date(text):
    """Parse komoot's ISO 8601 dates to a tz-aware UTC datetime (or None)."""
    if not text or not isinstance(text, str):
        return None
    text = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        # Strip fractional seconds, keep any timezone offset, and retry.
        m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.\d+(.*)", text)
        if not m:
            return None
        try:
            dt = datetime.fromisoformat(m.group(1) + m.group(2))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
