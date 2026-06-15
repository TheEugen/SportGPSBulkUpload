"""Find likely-duplicate tours in a komoot account.

The problem this solves: an activity tracked on two devices at once (a SIGMA ROX
and the komoot app) ends up as two tours komoot does NOT auto-dedupe, because the
two devices are stopped a few metres apart, so their start/end coordinates differ.

What actually distinguishes such a pair (confirmed against a real 200-tour account):

- **Source differs** — one tour is komoot-app *recorded*, the other a SIGMA *import*.
  komoot already dedupes within a source, so genuine leftovers are always
  cross-source. Two imports (or two recordings) on a day are different rides.
- **Distance is near-identical** — within a couple of percent (e.g. 55.88 vs
  56.08 km). This is the reliable signal.
- **Start time is NOT close** — the import is stored hours after the recording
  (a ~2 h, sometimes ~4 h, offset from komoot's timezone handling), so an exact
  start-time match (an earlier, wrong assumption) misses everything. Time is used
  only as a loose same-ride window (default 6 h) to avoid pairing different days.
- **Duration is useless** — komoot recordings often leave the timer running, so a
  ride's recorded duration can be wildly larger than the import's moving time.

So matching is: cross-source + near-equal distance, within a wide time window.
This module never deletes anything — it only reports candidates for review.

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
        source=_source_category(raw),
    )


def find_duplicate_groups(tours, time_window_s=21600, distance_tol=0.15,
                          distance_abs_m=1000.0):
    """Group tours that look like the same ride captured on two devices.

    Two tours match when ALL of:

    - their distances are close — within ``distance_tol`` relative (0.15 = 15%)
      OR ``distance_abs_m`` absolute (so small rides differing by <1 km match);
    - they are not the same known source (a recorded tour pairs with an import,
      never recording-with-recording or import-with-import — komoot already
      dedupes those); tours of unknown source are not excluded;
    - their start times are within ``time_window_s`` (default 6 h) — a loose
      same-ride guard, because the two sources' timestamps can be ~2–4 h apart.

    (If either distance is missing, distance is treated as a match so nothing is
    silently dropped.) Matching is transitive, so 3 captures form one group.

    Returns a list of groups (each a list of >= 2 :class:`Tour`), newest first;
    tours within a group are ordered by start time.
    """
    items = [t for t in tours if t.start is not None]
    items.sort(key=lambda t: t.start)

    # Union-find over the time-sorted list: only tours within the window can
    # match, so the inner loop breaks as soon as the gap exceeds it.
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
            if _same_source(items[i], items[j]):
                continue
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

_KNOWN_SOURCES = ("recorded", "import")


def _distance_matches(a, b, rel, abs_m):
    if a.distance_m is None or b.distance_m is None:
        return True  # can't compare distance; let the other criteria decide
    diff = abs(a.distance_m - b.distance_m)
    if diff <= abs_m:
        return True
    return diff / max(a.distance_m, b.distance_m, 1.0) <= rel


def _same_source(a, b):
    """True only when both tours have the SAME known source (recorded/import).

    Such pairs are not cross-source duplicates — komoot already dedupes within a
    source — so they're excluded. Unknown sources never count as a conflict.
    """
    return (a.source in _KNOWN_SOURCES and b.source in _KNOWN_SOURCES
            and a.source == b.source)


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


def _source_category(raw):
    """Classify a tour's origin as 'recorded', 'import', or 'other'.

    komoot tags a tour's source in a `source` field whose api/type mentions
    e.g. ".../tour/recorded" or ".../tour/import". Recorded = tracked in the
    komoot app; import = uploaded file (the SIGMA export). This both drives
    cross-source matching and tells the user which tour is which in the report.
    Robust to `source` being a dict or a string, and to truncation.
    """
    source = raw.get("source")
    if isinstance(source, dict):
        source = " ".join(str(v) for v in source.values())
    text = (str(source) if source is not None else "").lower()
    if "record" in text:
        return "recorded"
    if "import" in text or "upload" in text:
        return "import"
    return "other"


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
