"""Lightweight activity-file inspection using only the standard library.

GPX and TCX are XML and are parsed for a title and elapsed time. FIT is a
binary format we don't parse here; callers fall back to the filename stem.
"""

import os
import xml.etree.ElementTree as ET
from datetime import datetime

# Extensions we can read metadata from by parsing them as XML.
XML_EXTS = (".gpx", ".tcx")


def _localname(tag):
    """Strip the XML namespace, e.g. '{...}name' -> 'name'."""
    return tag.rsplit("}", 1)[-1]


def read_metadata(path):
    """Return (name, elapsed_seconds) for an activity file.

    `name` is the first GPX <name> found, or None — TCX is skipped for naming
    because its <Name> elements hold the device/creator (e.g. "ROX GPS 11.0"),
    not a tour title, so those files fall back to the filename. `elapsed_seconds`
    is max-min of all <time>/<Time> values (read from GPX and TCX alike), or None
    when unknown. Binary formats (FIT) and malformed files yield (None, None).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in XML_EXTS:
        return None, None
    read_name = ext == ".gpx"

    name = None
    timestamps = []
    try:
        for _, elem in ET.iterparse(path, events=("end",)):
            tag = _localname(elem.tag).lower()
            if (read_name and tag == "name" and name is None
                    and elem.text and elem.text.strip()):
                name = elem.text.strip()
            elif tag == "time" and elem.text and elem.text.strip():
                dt = _parse_iso(elem.text.strip())
                if dt is not None:
                    timestamps.append(dt)
            elem.clear()
    except (ET.ParseError, OSError):
        pass

    elapsed = None
    if len(timestamps) >= 2:
        elapsed = int((max(timestamps) - min(timestamps)).total_seconds())
    return name, elapsed


def _parse_iso(value):
    # GPX uses ISO 8601 UTC, typically ending in 'Z'.
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
