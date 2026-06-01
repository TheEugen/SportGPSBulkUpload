"""Resumable upload state, keyed by file content hash."""

import hashlib
import json
import os


def file_hash(path):
    """SHA-1 of a file's bytes (identifies a GPX regardless of its path)."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def activity_key(path):
    """A cross-format key for an activity: the lower-cased filename stem.

    SIGMA exports the same activity to .gpx/.tcx/.fit with the same base name,
    so this lets us skip re-uploading an activity already sent in another format
    (komoot only dedupes same-format content, not e.g. a TCX against a GPX)."""
    return os.path.splitext(os.path.basename(path))[0].lower()


class UploadState:
    """JSON-backed record of which files have already been uploaded."""

    def __init__(self, path):
        self.path = path
        self.data = {"uploads": {}}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (ValueError, OSError):
                self.data = {"uploads": {}}
        self.data.setdefault("uploads", {})

    def is_done(self, file_hash_):
        rec = self.data["uploads"].get(file_hash_)
        return rec is not None and rec.get("status") in ("created", "duplicate")

    def done_activity_keys(self):
        """Activity keys already uploaded (created/duplicate), for cross-format
        dedupe. Falls back to deriving the key from each record's stored file
        path, so activities uploaded before this field existed still count."""
        keys = set()
        for rec in self.data["uploads"].values():
            if rec.get("status") not in ("created", "duplicate"):
                continue
            key = rec.get("activity_key")
            if not key and rec.get("file"):
                key = os.path.splitext(os.path.basename(rec["file"]))[0].lower()
            if key:
                keys.add(key)
        return keys

    def get(self, file_hash_):
        return self.data["uploads"].get(file_hash_)

    def record(self, file_hash_, **fields):
        self.data["uploads"][file_hash_] = fields
        self.save()

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)
