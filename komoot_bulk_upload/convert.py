"""Convert activity files into GPX for upload to komoot.

We normalize TCX to GPX before upload so the body is always a format komoot is
known to accept. (komoot's importer also takes TCX/FIT directly via the
`data_type` query param — the real cause of the earlier upload failures was a
stray Content-Type header, see api.py — but converting TCX is a safe, verified
path.) GPX is passed through, TCX is converted with the stdlib, and FIT is not
yet supported (see TASKS.md task 14).
"""

import os
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

GPX_NS = "http://www.topografix.com/GPX/1/1"


class UnsupportedFormat(Exception):
    """Raised for a file we can't turn into komoot-acceptable GPX."""


def _localname(tag):
    """Strip the XML namespace, e.g. '{...}Trackpoint' -> 'Trackpoint'."""
    return tag.rsplit("}", 1)[-1]


def upload_payload(path):
    """Return (gpx_bytes, "gpx") ready to POST, converting if needed.

    Raises UnsupportedFormat for formats we can't convert yet (FIT).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gpx":
        with open(path, "rb") as f:
            return f.read(), "gpx"
    if ext == ".tcx":
        return tcx_to_gpx(path), "gpx"
    if ext == ".fit":
        raise UnsupportedFormat(
            "FIT upload is not implemented yet (komoot's API needs GPX; "
            "FIT is binary and needs a parser). Use the GPX export instead.")
    raise UnsupportedFormat("Unsupported file type: {}".format(ext or "<none>"))


def tcx_to_gpx(path):
    """Convert a Garmin TCX file to GPX 1.1 bytes (UTF-8).

    Pulls every Trackpoint's lat/lon (required), plus altitude and time when
    present, into a single track segment. Namespace-agnostic, stdlib only.
    """
    points = []        # (lat, lon, ele|None, time|None)
    start_time = None
    for _, elem in ET.iterparse(path, events=("end",)):
        if _localname(elem.tag) != "Trackpoint":
            continue
        lat = lon = ele = when = None
        for child in elem.iter():
            name = _localname(child.tag)
            text = (child.text or "").strip()
            if not text:
                continue
            if name == "LatitudeDegrees":
                lat = text
            elif name == "LongitudeDegrees":
                lon = text
            elif name == "AltitudeMeters":
                ele = text
            elif name == "Time":
                when = text
        if lat and lon:
            points.append((lat, lon, ele, when))
            if start_time is None and when:
                start_time = when
        elem.clear()

    if not points:
        raise UnsupportedFormat(
            "No track points with coordinates found in TCX.")

    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append('<gpx version="1.1" creator="SportGPSBulkUpload" xmlns="{}">'
               .format(GPX_NS))
    if start_time:
        out.append("<metadata><time>{}</time></metadata>".format(
            escape(start_time)))
    out.append("<trk><trkseg>")
    for lat, lon, ele, when in points:
        out.append('<trkpt lat="{}" lon="{}">'.format(escape(lat), escape(lon)))
        if ele:
            out.append("<ele>{}</ele>".format(escape(ele)))
        if when:
            out.append("<time>{}</time>".format(escape(when)))
        out.append("</trkpt>")
    out.append("</trkseg></trk></gpx>")
    return "\n".join(out).encode("utf-8")
