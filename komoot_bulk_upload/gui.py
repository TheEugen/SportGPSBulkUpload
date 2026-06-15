"""A minimal tkinter GUI for bulk-uploading activities to komoot.

Wraps the same backend used by the CLI (``api`` / ``gpx`` / ``state``):

- shows whether the program is signed in (LoggedIn / NotLoggedIn),
- lets the user pick a directory and a file format to upload, and
- drives a progress bar while uploading.

Network work (sign-in, uploads) runs on a worker thread; the worker posts
events onto a queue that the Tk main loop drains, so the UI stays responsive
and all widget updates happen on the main thread.
"""

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .api import (
    KomootClient, KomootError, KomootAuthError, PRIVACY, SPORTS, DATA_TYPES,
    data_type_for, german_activity_name,
)
from .cli import collect_files
from .payload import upload_payload, UnsupportedFormat
from .gpx import read_metadata
from .state import UploadState, file_hash, activity_key
from .logfile import RunLog, DEFAULT_LOG_FILE
from .dedupe import parse_tour, find_duplicate_groups, format_groups
from .backup import backup_tour, BackupError

# Format choices for the dropdown: "all" plus each supported extension.
FORMAT_CHOICES = ("all",) + tuple(DATA_TYPES)


class KomootUploaderGUI:
    def __init__(self, root, default_sport="touringbicycle",
                 default_status="private", delay=2.0,
                 state_file="komoot_upload_state.json",
                 log_file=DEFAULT_LOG_FILE):
        self.root = root
        self.delay = delay
        self.state_file = state_file
        self.log_file = log_file

        self.client = None          # set once sign-in succeeds
        self.username = None
        self.events = queue.Queue()  # worker -> main-thread messages
        self.uploading = False

        # Duplicate-deletion options.
        self.backup_dir = "komoot_tour_backups"
        self.backup_var = tk.BooleanVar(value=True)  # back up before deleting

        root.title("Sport GPS Bulk Upload to komoot")
        root.minsize(560, 460)

        self._build_credentials()
        self._build_source(default_sport, default_status)
        self._build_progress()
        self._build_log()

        # Pre-fill from environment, if present, as a convenience.
        self.email_var.set(os.environ.get("KOMOOT_EMAIL", ""))
        self.password_var.set(os.environ.get("KOMOOT_PASSWORD", ""))

        self._refresh_login_status()
        self._refresh_summary()
        self.root.after(100, self._drain_events)

    # --- UI construction -------------------------------------------------

    def _build_credentials(self):
        f = ttk.LabelFrame(self.root, text="komoot account", padding=10)
        f.pack(fill="x", padx=10, pady=(10, 5))
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Email:").grid(row=0, column=0, sticky="w")
        self.email_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.email_var).grid(
            row=0, column=1, sticky="ew", padx=5)

        ttk.Label(f, text="Password:").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.password_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.password_var, show="•").grid(
            row=1, column=1, sticky="ew", padx=5, pady=(5, 0))

        self.signin_btn = ttk.Button(f, text="Sign in", command=self._on_signin)
        self.signin_btn.grid(row=0, column=2, rowspan=2, sticky="ns", padx=(5, 0))

        self.login_var = tk.StringVar()
        self.login_label = ttk.Label(f, textvariable=self.login_var)
        self.login_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _build_source(self, default_sport, default_status):
        f = ttk.LabelFrame(self.root, text="Activities to upload", padding=10)
        f.pack(fill="x", padx=10, pady=5)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Directory:").grid(row=0, column=0, sticky="w")
        self.dir_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.dir_var, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=5)
        ttk.Button(f, text="Browse…", command=self._on_browse).grid(
            row=0, column=2)

        ttk.Label(f, text="Format:").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.format_var = tk.StringVar(value="all")
        fmt = ttk.Combobox(f, textvariable=self.format_var, values=FORMAT_CHOICES,
                           state="readonly", width=10)
        fmt.grid(row=1, column=1, sticky="w", padx=5, pady=(5, 0))
        fmt.bind("<<ComboboxSelected>>", lambda _e: self._refresh_summary())

        ttk.Label(f, text="Sport:").grid(row=2, column=0, sticky="w", pady=(5, 0))
        self.sport_var = tk.StringVar(value=default_sport)
        sport_cb = ttk.Combobox(f, textvariable=self.sport_var, values=SPORTS,
                                width=18)
        sport_cb.grid(row=2, column=1, sticky="w", padx=5, pady=(5, 0))
        sport_cb.bind("<<ComboboxSelected>>", self._on_sport_changed)
        sport_cb.bind("<KeyRelease>", self._on_sport_changed)

        ttk.Label(f, text="Name:").grid(row=3, column=0, sticky="w", pady=(5, 0))
        self._name_default = german_activity_name(default_sport)
        self.name_var = tk.StringVar(value=self._name_default)
        ttk.Entry(f, textvariable=self.name_var).grid(
            row=3, column=1, columnspan=2, sticky="ew", padx=5, pady=(5, 0))

        ttk.Label(f, text="Privacy:").grid(row=4, column=0, sticky="w", pady=(5, 0))
        self.status_var = tk.StringVar(value=default_status)
        ttk.Combobox(f, textvariable=self.status_var, values=PRIVACY,
                     state="readonly", width=10).grid(
            row=4, column=1, sticky="w", padx=5, pady=(5, 0))

        self.summary_var = tk.StringVar()
        ttk.Label(f, textvariable=self.summary_var, foreground="#555").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _on_sport_changed(self, _event=None):
        """Keep the name field on the German default until the user edits it."""
        new_default = german_activity_name(self.sport_var.get().strip())
        if self.name_var.get().strip() in ("", self._name_default):
            self.name_var.set(new_default)
        self._name_default = new_default

    def _build_progress(self):
        f = ttk.Frame(self.root, padding=(10, 5))
        f.pack(fill="x")
        f.columnconfigure(0, weight=1)

        self.progress = ttk.Progressbar(f, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")

        self.progress_var = tk.StringVar(value="Idle")
        ttk.Label(f, textvariable=self.progress_var).grid(
            row=0, column=1, padx=(8, 0))

        self.dupes_btn = ttk.Button(f, text="Find duplicates",
                                    command=self._on_find_duplicates)
        self.dupes_btn.grid(row=0, column=2, padx=(8, 0))

        self.upload_btn = ttk.Button(f, text="Upload", command=self._on_upload)
        self.upload_btn.grid(row=0, column=3, padx=(8, 0))

    def _build_log(self):
        f = ttk.LabelFrame(self.root, text="Log", padding=5)
        f.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.log = tk.Text(f, height=10, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(f, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # --- helpers ---------------------------------------------------------

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh_login_status(self):
        if self.client is not None:
            who = " as user " + str(self.username) if self.username else ""
            self.login_var.set("● LoggedIn" + who)
            self.login_label.configure(foreground="#1a7f37")  # green
        else:
            self.login_var.set("● NotLoggedIn")
            self.login_label.configure(foreground="#cf222e")  # red

    def _matching_files(self):
        directory = self.dir_var.get()
        if not directory:
            return []
        files = collect_files([directory])
        fmt = self.format_var.get()
        if fmt != "all":
            files = [f for f in files if data_type_for(f) == fmt]
        return files

    def _refresh_summary(self):
        directory = self.dir_var.get() or "(none selected)"
        fmt = self.format_var.get()
        count = len(self._matching_files()) if self.dir_var.get() else 0
        self.summary_var.set(
            "Directory: {}   |   Format: {}   |   {} file(s)".format(
                directory, fmt, count))

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.signin_btn.configure(state=state)
        self.upload_btn.configure(state=state)
        self.dupes_btn.configure(state=state)

    # --- event handlers --------------------------------------------------

    def _on_browse(self):
        directory = filedialog.askdirectory(title="Choose a folder of activities")
        if directory:
            self.dir_var.set(directory)
            self._refresh_summary()

    def _on_signin(self):
        email = self.email_var.get().strip()
        password = self.password_var.get()
        if not email or not password:
            self._log("Enter both an email and a password to sign in.")
            return
        self._set_busy(True)
        self.progress_var.set("Signing in…")
        self._log("Signing in as {}…".format(email))

        def work():
            try:
                client = KomootClient(email, password=password)
                username = client.signin()
                self.events.put(("signin_ok", client, username))
            except KomootAuthError as e:
                self.events.put(("signin_err", str(e)))
            except KomootError as e:
                self.events.put(("signin_err", str(e)))
            except Exception as e:  # network/other
                self.events.put(("signin_err", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _on_find_duplicates(self, time_window_min=360.0):
        """List likely-duplicate tours already in the account (read-only)."""
        if self.uploading:
            return
        if self.client is None:
            self._log("Sign in before searching for duplicates.")
            return

        self._set_busy(True)
        self.progress_var.set("Searching…")
        self._log("Fetching recorded tours from komoot…")
        log = RunLog(self.log_file)
        window_s = int(time_window_min * 60)

        def work():
            try:
                tours = [parse_tour(t) for t in self.client.list_tours()]
            except KomootError as e:
                self.events.put(("dupes_err", str(e)))
                return
            except Exception as e:  # network/other
                self.events.put(("dupes_err", str(e)))
                return
            groups = find_duplicate_groups(tours, time_window_s=window_s)
            lines = ["Found {} recorded tour(s).".format(len(tours))]
            if not groups:
                lines.append("No likely duplicates found (cross-source, "
                             "near-equal distance, within {:g} min).".format(
                                 time_window_min))
            else:
                dup_total = sum(len(g) for g in groups)
                lines.append(
                    "Found {} duplicate group(s) covering {} tours (cross-source, "
                    "near-equal distance). Nothing was deleted — review and "
                    "remove in komoot:".format(len(groups), dup_total))
                lines.append("")
                lines.extend(format_groups(groups))
            for line in lines:
                log.log(line)
            log.close()
            self.events.put(("dupes_ok", lines, groups))

        threading.Thread(target=work, daemon=True).start()

    def _open_dupes_window(self, groups):
        """Show duplicate groups, each tour with a confirm-then-delete button."""
        win = tk.Toplevel(self.root)
        win.title("Duplicate tours — review and delete")
        win.minsize(720, 420)

        canvas = tk.Canvas(win, borderwidth=0)
        scroll = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        ttk.Label(inner, foreground="#cf222e",
                  text="Deleting a tour is permanent. Keep at least one tour "
                       "per group.").pack(anchor="w", padx=10, pady=(10, 2))
        ttk.Checkbutton(
            inner, variable=self.backup_var,
            text="Back up each tour to a local GPX (in {}) before deleting".format(
                os.path.abspath(self.backup_dir))).pack(anchor="w", padx=10,
                                                         pady=(0, 6))

        for n, group in enumerate(groups, 1):
            ttk.Label(inner, text="Group {} — {} tours".format(n, len(group)),
                      font=("", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 2))
            for tour in group:
                row = ttk.Frame(inner)
                row.pack(fill="x", padx=16, pady=2)
                text = tour.summary().replace("\n        ", "   ")
                ttk.Label(row, text=text).pack(side="left", anchor="w")
                btn = ttk.Button(row, text="Delete")
                btn.configure(command=lambda t=tour, b=btn: self._on_delete_tour(t, b))
                btn.pack(side="right", padx=(8, 0))

    def _on_delete_tour(self, tour, btn):
        if self.client is None:
            return
        if not messagebox.askyesno(
                "Confirm deletion",
                "Permanently delete tour {} ({!r})?\nThis cannot be undone.".format(
                    tour.id, tour.name or "")):
            return
        btn.configure(state="disabled", text="Deleting…")
        backup = self.backup_var.get()

        def work():
            try:
                if backup:
                    path = backup_tour(self.client, tour.id, self.backup_dir)
                    self.events.put(("log", "Backed up tour {} -> {}".format(
                        tour.id, path)))
                self.client.delete_tour(tour.id)
                self.events.put(("delete_ok", tour.id, btn))
            except Exception as e:  # KomootError / BackupError / network
                self.events.put(("delete_err", tour.id, str(e), btn))

        threading.Thread(target=work, daemon=True).start()

    def _on_upload(self):
        if self.uploading:
            return
        if self.client is None:
            self._log("Sign in before uploading.")
            return
        files = self._matching_files()
        if not files:
            self._log("No matching files in the selected directory.")
            return

        self.uploading = True
        self._set_busy(True)
        self.progress.configure(maximum=len(files), value=0)
        self.progress_var.set("0 / {}".format(len(files)))
        self._log("Uploading {} file(s)…".format(len(files)))

        sport = self.sport_var.get().strip()
        status = self.status_var.get()
        title = self.name_var.get().strip() or german_activity_name(sport)
        state = UploadState(self.state_file)
        done_keys = state.done_activity_keys()
        log = RunLog(self.log_file)

        def work():
            counts = {"created": 0, "duplicate": 0, "skipped": 0, "failed": 0}
            total = len(files)
            log.log("Uploading {} file(s) (sport={}, name={!r}, status={}).".format(
                total, sport, title, status))

            def progress(i, msg):
                """Show a line in the GUI and mirror it to the log file."""
                log.log(msg)
                self.events.put(("progress", i, total, msg))

            for i, path in enumerate(files, 1):
                base = os.path.basename(path)
                digest = file_hash(path)
                akey = activity_key(path)
                if state.is_done(digest) or akey in done_keys:
                    counts["skipped"] += 1
                    progress(i, "[{}/{}] {} -> skipped (already uploaded)".format(
                        i, total, base))
                    continue
                _, elapsed = read_metadata(path)
                try:
                    data, dtype = upload_payload(path)
                except UnsupportedFormat as e:
                    counts["failed"] += 1
                    progress(i, "[{}/{}] {} -> FAILED: {}".format(i, total, base, e))
                    continue
                try:
                    result = self.client.upload_tour(
                        data, name=title, sport=sport,
                        data_type=dtype, status=status)
                except KomootError as e:
                    counts["failed"] += 1
                    state.record(digest, status="failed", file=path,
                                 name=title, activity_key=akey, error=str(e))
                    progress(i, "[{}/{}] {} -> FAILED: {}".format(i, total, base, e))
                    continue
                counts[result.status] += 1
                state.record(digest, status=result.status, file=path,
                             name=title, activity_key=akey,
                             tour_id=result.tour_id)
                done_keys.add(akey)
                progress(i, "[{}/{}] {} -> {}".format(
                    i, total, base, result.status))
                if i < total and self.delay > 0:
                    time.sleep(self.delay)
            log.log("Done. created={created} duplicate={duplicate} "
                    "skipped={skipped} failed={failed}".format(**counts))
            log.close()
            self.events.put(("done", counts))

        threading.Thread(target=work, daemon=True).start()

    # --- main-thread event pump -----------------------------------------

    def _drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "signin_ok":
                    _, self.client, self.username = event
                    self._refresh_login_status()
                    self._log("Signed in.")
                    self.progress_var.set("Idle")
                    self._set_busy(False)
                elif kind == "signin_err":
                    self._log("Sign-in failed: {}".format(event[1]))
                    self.progress_var.set("Idle")
                    self._set_busy(False)
                elif kind == "dupes_ok":
                    for line in event[1]:
                        self._log(line)
                    self.progress_var.set("Idle")
                    self._set_busy(False)
                    if self.log_file:
                        self._log("Report written to {}".format(
                            os.path.abspath(self.log_file)))
                    if event[2]:
                        self._open_dupes_window(event[2])
                elif kind == "dupes_err":
                    self._log("Duplicate search failed: {}".format(event[1]))
                    self.progress_var.set("Idle")
                    self._set_busy(False)
                elif kind == "log":
                    self._log(event[1])
                elif kind == "delete_ok":
                    _, tid, btn = event
                    btn.configure(text="Deleted", state="disabled")
                    self._log("Deleted tour {}.".format(tid))
                elif kind == "delete_err":
                    _, tid, msg, btn = event
                    btn.configure(text="Delete", state="normal")
                    self._log("Failed to delete tour {}: {}".format(tid, msg))
                elif kind == "progress":
                    _, i, total, msg = event
                    self.progress.configure(value=i)
                    self.progress_var.set("{} / {}".format(i, total))
                    self._log(msg)
                elif kind == "done":
                    counts = event[1]
                    self.uploading = False
                    self._set_busy(False)
                    self.progress_var.set("Done")
                    self._log("Done. created={created} duplicate={duplicate} "
                              "skipped={skipped} failed={failed}".format(**counts))
                    if self.log_file:
                        self._log("Log written to {}".format(
                            os.path.abspath(self.log_file)))
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)


def run(args=None):
    """Launch the GUI. `args` may carry CLI defaults (sport/status/delay/...)."""
    root = tk.Tk()
    kwargs = {}
    if args is not None:
        kwargs = dict(
            default_sport=getattr(args, "sport", "touringbicycle"),
            default_status=getattr(args, "status", "private"),
            delay=getattr(args, "delay", 2.0),
            state_file=getattr(args, "state_file", "komoot_upload_state.json"),
            log_file="" if getattr(args, "no_log", False)
                     else getattr(args, "log_file", DEFAULT_LOG_FILE),
        )
    KomootUploaderGUI(root, **kwargs)
    root.mainloop()
    return 0


if __name__ == "__main__":
    run()
