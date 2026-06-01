# Sport GPS Bulk Upload to komoot

Upload many `.gpx`, `.tcx`, or `.fit` activities (e.g. exported from SIGMA DATA
CENTER 5.9.1) to a komoot account in one go. komoot's website only accepts one
activity at a time; this tool drives komoot's **undocumented internal API** to
upload a whole folder.

> ⚠️ The komoot endpoints used here are unofficial and may change or break without
> notice. Uploads default to **private** and the run is **resumable**, so you can
> safely re-run after an interruption without creating duplicates.

## Install

Requires Python 3.8+.

```powershell
pip install -r requirements.txt
```

## Usage

```powershell
# 1. Preview what would be uploaded (no network, no credentials needed):
py -m komoot_bulk_upload "C:\path\to\activities" --dry-run

# 2. Upload every .gpx/.tcx/.fit in a folder as private Bike Touring activities:
py -m komoot_bulk_upload "C:\path\to\activities" --sport touringbicycle --status private

# Or use the graphical interface:
py -m komoot_bulk_upload --gui
```

### GUI

`--gui` opens a small tkinter window that shows whether you're signed in
(**LoggedIn** / **NotLoggedIn**), lets you pick a directory and file format (with a
live count of matching files), and displays a progress bar while uploading. It
uses the same backend as the CLI, so uploads stay resumable.

Credentials are taken from, in order: `--email` / `--password`, then the
`KOMOOT_EMAIL` / `KOMOOT_PASSWORD` environment variables, then an interactive
prompt (the password prompt is hidden).

### Arguments

| Option | Default | Description |
| --- | --- | --- |
| `paths` | — | One or more `.gpx`/`.tcx`/`.fit` files, directories, or globs. |
| `--email` / `--password` | env / prompt | komoot credentials. |
| `--token` | — | Existing auth token; skips the sign-in step. |
| `--sport` | `touringbicycle` | komoot sport id applied to every tour (see below). |
| `--status` | `private` | `private`, `friends`, or `public`. |
| `--derive-time` | off | Send elapsed time (from GPX timestamps) as `time_in_motion`. |
| `--delay` | `2.0` | Seconds between uploads, to be gentle on the API. |
| `--state-file` | `komoot_upload_state.json` | Resume file; skips already-uploaded GPX. |
| `--force` | off | Re-upload even files marked done in the state file. |
| `--dry-run` | off | List planned uploads without contacting komoot. |

### Common sport ids

`touringbicycle` (Bike Touring), `e_touringbicycle`, `racebike` (Road Cycling),
`e_racebike`, `mtb`, `e_mtb`, `mtb_easy` (Gravel), `citybike`, `hike`, `jogging`.

## How it works

1. **Sign in** — `GET /v006/account/email/{email}/` with HTTP Basic Auth returns a
   session token; the client then authenticates subsequent calls with it.
2. **Upload** — each file is `POST`ed to `/v007/tours/` with the raw file body and
   `data_type` (`gpx`/`tcx`/`fit`, by extension) plus `sport` / `status` / `name`
   query params. `201` = created, `202` = duplicate.
3. **Resume** — every file's SHA-1 and outcome are written to the state file, so a
   re-run skips anything already created or detected as a duplicate.

GPX and TCX titles/elapsed time are read from the file; FIT is binary, so its
title falls back to the filename and elapsed time is left to komoot.

Recommended first run: `--dry-run`, then a single file, then the full batch.
