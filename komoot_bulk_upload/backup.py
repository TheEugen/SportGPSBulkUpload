"""Save a komoot tour to a local GPX file before it is deleted.

A safety net for the duplicate-delete flow: fetch a tour's track from komoot
(`KomootClient.get_tour`, which embeds the coordinate points) and write a plain
GPX 1.1 file locally, so a deleted tour can always be re-uploaded. Stdlib only.
"""

import os
from datetime import timedelta, timezone
from xml.sax.saxutils import escape

from .dedupe import _parse_date


class BackupError(Exception):
    """Raised when a tour can't be backed up (e.g. it has no track points)."""


def backup_tour(client, tour_id, dest_dir):
    """Fetch `tour_id` from komoot and write it as GPX under `dest_dir`.

    Returns the path written. Raises BackupError / KomootError on failure (the
    caller must not delete a tour whose backup failed).
    """
    tour = client.get_tour(tour_id)
    data = gpx_from_tour(tour)
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, _filename(tour, tour_id))
    with open(path, "wb") as f:
        f.write(data)
    return path


def gpx_from_tour(tour):
    """Build GPX 1.1 bytes from a komoot tour dict (with embedded coordinates)."""
    items = (((tour.get("_embedded") or {}).get("coordinates") or {})
             .get("items")) or []
    if not items:
        raise BackupError("tour {} has no coordinate data to back up".format(
            tour.get("id")))

    start = _parse_date(tour.get("date"))
    name = escape(str(tour.get("name") or "komoot tour"))
    sport = tour.get("sport")

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="SportGPSBulkUpload" '
        'xmlns="http://www.topografix.com/GPX/1/1">',
        '  <metadata><name>{}</name>{}</metadata>'.format(
            name, "<time>{}</time>".format(_iso(start)) if start else ""),
        '  <trk><name>{}</name>{}<trkseg>'.format(
            name, "<type>{}</type>".format(escape(str(sport))) if sport else ""),
    ]
    for it in items:
        lat, lng = it.get("lat"), it.get("lng")
        if lat is None or lng is None:
            continue
        extra = ""
        if it.get("alt") is not None:
            extra += "<ele>{}</ele>".format(it["alt"])
        if start is not None and it.get("t") is not None:
            extra += "<time>{}</time>".format(
                _iso(start + timedelta(milliseconds=it["t"])))
        out.append('    <trkpt lat="{}" lon="{}">{}</trkpt>'.format(lat, lng, extra))
    out.append("  </trkseg></trk>")
    out.append("</gpx>")
    return ("\n".join(out) + "\n").encode("utf-8")


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _filename(tour, tour_id):
    start = _parse_date(tour.get("date"))
    day = start.strftime("%Y-%m-%d") if start else "tour"
    return "{}_{}.gpx".format(day, tour_id)
