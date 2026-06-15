"""Command-line entry point: bulk-upload a set of GPX files to komoot."""

import argparse
import csv
import getpass
import glob
import os
import sys
import time

from . import __version__
from .api import (
    KomootClient, KomootError, KomootAuthError, PRIVACY, SPORTS,
    DATA_TYPES, data_type_for, german_activity_name,
)
from .payload import upload_payload, UnsupportedFormat
from .gpx import read_metadata
from .state import UploadState, file_hash, activity_key
from .logfile import RunLog, DEFAULT_LOG_FILE
from .dedupe import parse_tour, find_duplicate_groups, format_groups
from .backup import backup_tour, BackupError

# File extensions we can upload (matches api.DATA_TYPES keys).
SUPPORTED_EXTS = tuple("." + ext for ext in DATA_TYPES)


def build_parser():
    p = argparse.ArgumentParser(
        prog="komoot_bulk_upload",
        description="Bulk-upload GPX activities to komoot via its internal API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Credentials are read from --email/--password, then the env vars\n"
            "KOMOOT_EMAIL / KOMOOT_PASSWORD, then an interactive prompt.\n\n"
            "Common sports: " + ", ".join(SPORTS)
        ),
    )
    p.add_argument(
        "paths", nargs="*",
        help="GPX/TCX/FIT files and/or directories to upload.",
    )
    p.add_argument(
        "--gui", action="store_true",
        help="Launch the graphical interface instead of uploading from the CLI.",
    )
    p.add_argument(
        "--find-duplicates", action="store_true",
        help="Don't upload: list likely-duplicate tours already in your komoot "
             "account (same ride tracked on two devices) for manual review.",
    )
    p.add_argument(
        "--time-window", type=float, default=360.0,
        help="Max minutes between two tours' start times to still treat them as "
             "the same ride, for --find-duplicates (default: 360 = 6 h; the two "
             "sources' timestamps can be hours apart).",
    )
    p.add_argument(
        "--delete-duplicates", action="store_true",
        help="After --find-duplicates, interactively choose tours to DELETE from "
             "each group (asks for confirmation; needs an interactive terminal).",
    )
    p.add_argument(
        "--dump-tours", metavar="PATH",
        help="With --find-duplicates, write a CSV table of every fetched tour "
             "(id/date/distance/duration/sport) for diagnosing matches.",
    )
    p.add_argument(
        "--backup-dir", metavar="DIR",
        help="With --delete-duplicates, save each tour as a local GPX in DIR "
             "before deleting it (a tour whose backup fails is not deleted).",
    )
    p.add_argument("--email", help="komoot account email.")
    p.add_argument("--password", help="komoot password (prefer the prompt/env var).")
    p.add_argument("--token", help="Existing komoot auth token; skips sign-in.")
    p.add_argument(
        "--sport", default="touringbicycle",
        help="komoot sport id for every tour (default: touringbicycle).",
    )
    p.add_argument(
        "--name",
        help="Tour name for every upload (default: the German activity name "
             "for --sport, e.g. 'Fahrradtour').",
    )
    p.add_argument(
        "--status", default="private", choices=PRIVACY,
        help="Privacy of uploaded tours (default: private).",
    )
    p.add_argument(
        "--derive-time", action="store_true",
        help="Send elapsed time (from GPX timestamps) as time_in_motion.",
    )
    p.add_argument(
        "--delay", type=float, default=2.0,
        help="Seconds to wait between uploads (default: 2.0).",
    )
    p.add_argument(
        "--state-file", default="komoot_upload_state.json",
        help="Resume file tracking uploaded GPX (default: komoot_upload_state.json).",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Re-upload files even if the state file marks them done.",
    )
    p.add_argument(
        "--log-file", default=DEFAULT_LOG_FILE,
        help="Plaintext log of each upload, in the working directory "
             "(default: {}).".format(DEFAULT_LOG_FILE),
    )
    p.add_argument(
        "--no-log", action="store_true",
        help="Don't write the plaintext upload log.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="List what would be uploaded without contacting komoot.",
    )
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return p


def collect_files(paths):
    """Expand files/dirs/globs into a sorted, de-duplicated list of activity files.

    Matches the supported extensions (.gpx/.tcx/.fit), case-insensitively.
    """
    found = []
    for raw in paths:
        if os.path.isdir(raw):
            for ext in SUPPORTED_EXTS:
                found.extend(glob.glob(os.path.join(raw, "*" + ext)))
                found.extend(glob.glob(os.path.join(raw, "*" + ext.upper())))
        elif any(ch in raw for ch in "*?["):
            found.extend(glob.glob(raw))
        else:
            found.append(raw)
    files = [f for f in found
             if f.lower().endswith(SUPPORTED_EXTS) and os.path.isfile(f)]
    return sorted(dict.fromkeys(os.path.abspath(f) for f in files))


def resolve_credentials(args):
    email = args.email or os.environ.get("KOMOOT_EMAIL")
    password = args.password or os.environ.get("KOMOOT_PASSWORD")
    token = args.token
    if token:
        if not email:
            email = input("komoot email: ").strip()
        return email, None, token
    if not email:
        email = input("komoot email: ").strip()
    if not password:
        password = getpass.getpass("komoot password: ")
    return email, password, None


def find_duplicates_command(args):
    """Sign in, list recorded tours, and print likely-duplicate groups.

    Read-only: nothing is deleted — the report lists tour ids + web links so the
    user can review and remove duplicates by hand in komoot.
    """
    email, password, token = resolve_credentials(args)
    client = KomootClient(email, password=password, token=token)
    try:
        username = client.signin()
        print("Signed in to komoot{}.".format(
            " as user " + str(username) if username else ""))
    except KomootAuthError as e:
        print("Authentication failed: {}".format(e), file=sys.stderr)
        return 1
    except KomootError as e:
        print("Could not sign in: {}".format(e), file=sys.stderr)
        return 1

    print("Fetching recorded tours from komoot…")
    try:
        tours = [parse_tour(t) for t in client.list_tours()]
    except KomootError as e:
        print("Could not list tours: {}".format(e), file=sys.stderr)
        return 1

    window_s = int(args.time_window * 60)
    groups = find_duplicate_groups(tours, time_window_s=window_s)

    log = RunLog("" if args.no_log else args.log_file)

    def emit(line):
        print(line)
        log.log(line)

    dated = sum(1 for t in tours if t.start is not None)
    emit("Found {} recorded tour(s); {} have a usable start date.".format(
        len(tours), dated))
    if tours and dated == 0:
        emit("WARNING: no tour dates could be parsed — matching can't work. "
             "Re-run with --dump-tours tours.csv and share a few rows so the "
             "date format can be fixed.")
    if args.dump_tours:
        _write_tour_table(args.dump_tours, tours)
        emit("Wrote tour table to {}".format(os.path.abspath(args.dump_tours)))
    if not groups:
        emit("No likely duplicates found (cross-source, near-equal distance, "
             "within {:g} min).".format(args.time_window))
    else:
        dup_total = sum(len(g) for g in groups)
        emit("Found {} duplicate group(s) covering {} tours "
             "(cross-source, near-equal distance, within {:g} min). Nothing was "
             "deleted — review and remove these in komoot:".format(
                 len(groups), dup_total, args.time_window))
        emit("")
        for line in format_groups(groups):
            emit(line)

    if groups and args.delete_duplicates:
        if not sys.stdin.isatty():
            emit("--delete-duplicates needs an interactive terminal; skipping deletion.")
        else:
            _delete_duplicates_interactive(client, groups, emit, args.backup_dir)

    if log.active:
        print("Report written to {}".format(os.path.abspath(args.log_file)))
    log.close()
    return 0


def _write_tour_table(path, tours):
    """Write every tour to a CSV for diagnosing why duplicates do/don't match.

    Sorted by start time (undated tours last) so two recordings of one ride sit
    next to each other and any start-time offset between them is obvious.
    """
    dated = sorted((t for t in tours if t.start is not None), key=lambda t: t.start)
    undated = [t for t in tours if t.start is None]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "date_utc", "date_local", "distance_km",
                    "duration_s", "sport", "source", "name"])
        for t in dated + undated:
            w.writerow([
                t.id,
                t.start.strftime("%Y-%m-%d %H:%M:%S") if t.start else "",
                t.start.astimezone().strftime("%Y-%m-%d %H:%M:%S") if t.start else "",
                "{:.2f}".format(t.distance_m / 1000.0) if t.distance_m is not None else "",
                t.duration_s if t.duration_s is not None else "",
                t.sport or "", t.source or "", t.name or "",
            ])


def _parse_delete_selection(text, group):
    """Map a user entry like '1,3' to tour ids within `group` (ignores junk)."""
    ids = []
    for tok in text.replace(" ", "").split(","):
        if not tok:
            continue
        if not tok.isdigit() or not (1 <= int(tok) <= len(group)):
            print("    Ignoring invalid choice: {!r}".format(tok))
            continue
        ids.append(group[int(tok) - 1].id)
    return ids


def _delete_duplicates_interactive(client, groups, emit, backup_dir=None):
    """Walk each duplicate group and delete only what the user confirms.

    Nothing is removed without an explicit 'y' confirmation; 'q' stops early.
    When `backup_dir` is set, each tour is saved as a local GPX first, and a
    tour whose backup fails is NOT deleted.
    """
    print("\nInteractive deletion — pick tours to remove from each group.")
    print("Deletions are permanent and confirmed one group at a time.")
    if backup_dir:
        print("Each tour is backed up to {} before deletion.".format(
            os.path.abspath(backup_dir)))
    print()
    deleted = 0
    for n, group in enumerate(groups, 1):
        print("Group {} of {}:".format(n, len(groups)))
        for i, tour in enumerate(group, 1):
            print("  [{}] {}".format(i, tour.summary()))
        sel = input("  Delete which? e.g. '2' or '1,3'; Enter keeps all, 'q' "
                    "stops: ").strip().lower()
        if sel == "q":
            break
        ids = _parse_delete_selection(sel, group) if sel else []
        if not ids:
            print("  Kept all.\n")
            continue
        confirm = input("  Permanently delete tour(s) {}? [y/N]: ".format(
            ", ".join(str(i) for i in ids))).strip().lower()
        if confirm != "y":
            print("  Skipped.\n")
            continue
        for tid in ids:
            if backup_dir:
                try:
                    path = backup_tour(client, tid, backup_dir)
                    emit("  backed up tour {} -> {}".format(tid, path))
                except (KomootError, BackupError, OSError) as e:
                    emit("  SKIPPED tour {} (backup failed: {})".format(tid, e))
                    continue
            try:
                client.delete_tour(tid)
                deleted += 1
                emit("  deleted tour {}".format(tid))
            except KomootError as e:
                emit("  FAILED to delete tour {}: {}".format(tid, e))
        print()
    emit("Deletion finished. {} tour(s) deleted.".format(deleted))


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.gui:
        from .gui import run
        return run(args)

    if args.find_duplicates:
        return find_duplicates_command(args)

    if not args.paths:
        print("No paths given. Pass files/directories, or use --gui.",
              file=sys.stderr)
        return 2

    files = collect_files(args.paths)
    if not files:
        print("No .gpx/.tcx/.fit files found in the given paths.", file=sys.stderr)
        return 2
    print("Found {} activity file(s).".format(len(files)))

    if args.dry_run:
        title = args.name or german_activity_name(args.sport)
        for f in files:
            _, elapsed = read_metadata(f)
            print("  would upload: {!r}  (type={}, name={!r}, elapsed={}s)".format(
                os.path.basename(f), data_type_for(f), title, elapsed))
        return 0

    email, password, token = resolve_credentials(args)
    client = KomootClient(email, password=password, token=token)
    try:
        username = client.signin()
        print("Signed in to komoot{}.".format(
            " as user " + str(username) if username else ""))
    except KomootAuthError as e:
        print("Authentication failed: {}".format(e), file=sys.stderr)
        return 1
    except KomootError as e:
        print("Could not sign in: {}".format(e), file=sys.stderr)
        return 1

    state = UploadState(args.state_file)
    done_keys = state.done_activity_keys()
    title = args.name or german_activity_name(args.sport)
    counts = {"created": 0, "duplicate": 0, "skipped": 0, "failed": 0}
    total = len(files)

    log = RunLog("" if args.no_log else args.log_file)

    def emit(line):
        print(line)
        log.log(line)

    emit("Uploading {} file(s) (sport={}, name={!r}, status={}).".format(
        total, args.sport, title, args.status))

    for i, path in enumerate(files, 1):
        prefix = "[{}/{}] {}".format(i, total, os.path.basename(path))
        digest = file_hash(path)
        akey = activity_key(path)

        if not args.force and state.is_done(digest):
            emit(prefix + " -> skipped (already uploaded)")
            counts["skipped"] += 1
            continue
        if not args.force and akey in done_keys:
            emit(prefix + " -> skipped (same activity already uploaded)")
            counts["skipped"] += 1
            continue

        _, elapsed = read_metadata(path)
        try:
            data, dtype = upload_payload(path)
        except UnsupportedFormat as e:
            emit(prefix + " -> FAILED: {}".format(e))
            counts["failed"] += 1
            continue  # not recorded, so a later run can retry once supported
        try:
            result = client.upload_tour(
                data, name=title, sport=args.sport, data_type=dtype,
                status=args.status,
                time_in_motion=elapsed if args.derive_time else None,
            )
        except KomootError as e:
            emit(prefix + " -> FAILED: {}".format(e))
            counts["failed"] += 1
            state.record(digest, status="failed", file=path, name=title,
                         activity_key=akey, error=str(e))
            continue

        counts[result.status] += 1
        emit(prefix + " -> {}{}".format(
            result.status, " (id " + str(result.tour_id) + ")" if result.tour_id else ""))
        state.record(digest, status=result.status, file=path, name=title,
                     activity_key=akey, tour_id=result.tour_id)
        done_keys.add(akey)

        if i < total and args.delay > 0:
            time.sleep(args.delay)

    summary = ("Done. created={created} duplicate={duplicate} "
               "skipped={skipped} failed={failed}".format(**counts))
    print("\n" + summary)
    log.log(summary)
    if log.active:
        print("Log written to {}".format(os.path.abspath(args.log_file)))
    log.close()
    return 1 if counts["failed"] else 0
