"""Desktop interface for browsing local IB Circlio reports."""

import argparse
import queue
import sqlite3
import subprocess
import sys
import threading
import tkinter as tk
import zipfile
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from PIL import Image, ImageDraw, ImageOps, ImageTk, UnidentifiedImageError

import launcher
import result
import server


BACKGROUND = "#f3f5f9"
SURFACE = "#ffffff"
TEXT = "#172033"
SUBTLE = "#738096"
ACCENT = "#5265e8"
GREEN = "#12805c"
RED = "#c44455"
SIDEBAR = "#17213a"


def compare_members(connection, profile, from_snapshot, to_snapshot, relationships):
    """Return snapshot membership differences for the selected relationships."""
    before = result.collection_for_date(connection, profile, from_snapshot)
    after = result.collection_for_date(connection, profile, to_snapshot)
    if before[1] >= after[1]:
        raise ValueError("From must be an older snapshot than To.")
    differences = []
    for relationship in relationships:
        old = {row[0] for row in result._members(connection, before[0], relationship)}
        new = {row[0] for row in result._members(connection, after[0], relationship)}
        differences.extend(
            (relationship.title(), "Added", username)
            for username in sorted(new - old)
        )
        differences.extend(
            (relationship.title(), "Removed", username)
            for username in sorted(old - new)
        )
    return before, after, differences


def browse_members(connection, collection_id, relationship_choice):
    """Return one row per account, marking list membership when both are selected."""
    selected = (
        result.RELATIONSHIPS
        if relationship_choice.lower() == "both"
        else (relationship_choice.lower(),)
    )
    memberships = {}
    for relationship in selected:
        for username, avatar_path in result._members(
            connection, collection_id, relationship
        ):
            memberships.setdefault(username, {})[relationship] = avatar_path

    rows = []
    for username, account_lists in sorted(memberships.items()):
        if len(selected) == 1:
            marker = selected[0].title()
        elif len(account_lists) == 2:
            marker = "[=] Both"
        elif "followers" in account_lists:
            marker = "[F] Followers only"
        else:
            marker = "[>] Following only"
        avatar_path = next(
            (path for path in account_lists.values() if path), None
        )
        rows.append((marker, username, avatar_path))
    return rows


def filter_browse_members(rows, membership_filter):
    """Filter combined-list rows by which direction of the relationship applies."""
    markers = {
        "follows profile only": "[F] Followers only",
        "profile follows only": "[>] Following only",
        "mutual follows": "[=] Both",
    }
    marker = markers.get(membership_filter.casefold())
    if marker is None:
        return rows
    return [row for row in rows if row[0] == marker]


def comparison_timeline(connection, profile, from_collection_id, to_collection_id, relationships):
    """Return every membership transition after the older snapshot through the newer one."""
    snapshots = connection.execute(
        """SELECT c.id, c.captured_at
           FROM collections c JOIN profiles p ON p.id = c.profile_id
           WHERE p.username = ? AND c.complete = 1
           ORDER BY c.captured_at, c.id""",
        (profile,),
    ).fetchall()
    positions = {row[0]: index for index, row in enumerate(snapshots)}
    if from_collection_id not in positions or to_collection_id not in positions:
        raise ValueError("Both comparison snapshots must be complete collections for this profile.")
    from_index = positions[from_collection_id]
    to_index = positions[to_collection_id]
    if from_index >= to_index:
        raise ValueError("From must be an older snapshot than To.")

    previous = {
        relationship: {
            row[0] for row in result._members(
                connection, from_collection_id, relationship
            )
        }
        for relationship in relationships
    }
    events = []
    for snapshot in snapshots[from_index + 1:to_index + 1]:
        for relationship in relationships:
            current = {
                row[0] for row in result._members(
                    connection, snapshot[0], relationship
                )
            }
            events.extend(
                (snapshot[1], snapshot[0], relationship.title(), "Added", username)
                for username in sorted(current - previous[relationship])
            )
            events.extend(
                (snapshot[1], snapshot[0], relationship.title(), "Removed", username)
                for username in sorted(previous[relationship] - current)
            )
            previous[relationship] = current
    return snapshots[from_index], snapshots[to_index], events


def collection_count_audit(connection, collection):
    """Report Instagram header totals against distinct stored usernames."""
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(collections)")
    }
    labels = None
    if {"followers_header_text", "following_header_text"}.issubset(columns):
        labels = connection.execute(
            """SELECT followers_header_text, following_header_text
               FROM collections WHERE id = ?""",
            (collection[0],),
        ).fetchone()
    entries = []
    for relationship, shown, label in zip(
        result.RELATIONSHIPS,
        (collection[3], collection[4]),
        (labels[0], labels[1]) if labels else (None, None),
    ):
        collected = result.count_members(connection, collection[0], relationship)
        recorded_shown = shown
        invalid_reason = None
        if relationship == "following" and shown is not None and shown > 7500:
            shown = None
            invalid_reason = "The stored total exceeds Instagram's 7,500-account following limit."
        elif shown is not None and shown != collected:
            shown = None
            invalid_reason = "The Instagram total did not match the collected usernames."
        entries.append({
            "relationship": relationship,
            "shown": shown,
            "recorded_shown": recorded_shown,
            "collected": collected,
            "difference": (
                collected - recorded_shown if recorded_shown is not None else None
            ),
            "source_label": label,
            "invalid_reason": invalid_reason,
        })
    return entries


def collection_avatar_coverage(connection, collection_id):
    total = sum(
        result.count_members(connection, collection_id, relationship)
        for relationship in result.RELATIONSHIPS
    )
    saved = connection.execute(
        """SELECT COUNT(*)
           FROM collection_avatar_versions cav
           JOIN collection_memberships cm
             ON cm.collection_id = cav.collection_id AND cm.user_id = cav.user_id
           WHERE cav.collection_id = ?""",
        (collection_id,),
    ).fetchone()[0]
    return {"total": total, "saved": saved}


def snapshot_label(collection_id, captured_at):
    try:
        timestamp = datetime.fromisoformat(captured_at)
        timestamp = timestamp.astimezone().strftime("%Y-%m-%d · %H:%M:%S %Z")
    except (TypeError, ValueError):
        timestamp = captured_at
    return f"{timestamp} · #{collection_id}"


def snapshot_choices(connection, profile):
    rows = connection.execute(
        """SELECT c.id, c.captured_at
           FROM collections c JOIN profiles p ON p.id = c.profile_id
           WHERE p.username = ? AND c.complete = 1
           ORDER BY c.captured_at, c.id""",
        (profile,),
    ).fetchall()
    return [(snapshot_label(row[0], row[1]), row[0]) for row in rows]


def matching_tree_items(items, query):
    """Return table item IDs whose displayed values contain the search text."""
    normalized_query = query.strip().casefold()
    if not normalized_query:
        return {item_id for item_id, _values in items}
    return {
        item_id
        for item_id, values in items
        if any(normalized_query in str(value).casefold() for value in values)
    }


class CirclioReportApp:
    def __init__(self, root, connection, data_dir):
        self.root = root
        self.connection = connection
        self.data_dir = Path(data_dir)
        self.app_directory = (
            Path(sys.executable).parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent
        )
        self.server_process = None
        self.server_stopping = False
        self.avatar_window = None
        self.server_messages = queue.Queue()
        self.server_status = tk.StringVar(value="Stopped")
        self.server_start_button = None
        self.server_stop_button = None
        self.server_log_view = None
        self.profile_values = self._profiles()
        self.profile = tk.StringVar(value=self.profile_values[0] if self.profile_values else "")
        self.dates = []
        self.snapshots = []
        self.snapshot_ids = {}
        self.latest_collection = None
        self.avatar_photos = {}
        self.avatar_errors = set()
        self.avatar_placeholder = None
        self.avatar_paths_by_item = {}
        self.tree_headers = {}
        self.tree_items = {}
        self.tree_search_vars = {}
        self.tree_search_status = {}
        self.excel_icon = self._make_excel_icon()
        self.root.title("IB_Circlio")
        self.root.geometry("1240x840")
        self.root.minsize(980, 680)
        self.root.configure(background=BACKGROUND)
        icon = self.app_directory / "icons" / "ib-circlio.ico"
        if icon.is_file():
            self.root.iconbitmap(str(icon))
        self._configure_styles()
        self._build_layout()
        self.root.bind_all("<Button-1>", self._close_avatar_on_app_click, add="+")
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            launcher.prepare_local_config(
                self.data_dir, self.app_directory / "config.js"
            )
        except OSError as error:
            self._append_server_log(f"Could not prepare local configuration: {error}")
            self.server_status.set("Configuration error")
        self.refresh()
        if self.snapshots:
            self.notebook.select(self.snapshot_tab)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(200, self._poll_server)

    def _profiles(self):
        return [
            row[0] for row in self.connection.execute(
                "SELECT username FROM profiles ORDER BY username"
            ).fetchall()
        ]

    def _configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
        style.configure("Muted.TLabel", background=BACKGROUND, foreground=SUBTLE)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 23), foreground=TEXT)
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 13), foreground=TEXT)
        style.configure("CardValue.TLabel", font=("Segoe UI Semibold", 24), foreground=TEXT)
        style.configure("CardTitle.TLabel", foreground=SUBTLE)
        style.configure(
            "TButton", padding=(13, 8), background="#e8ebf3",
            foreground=TEXT, borderwidth=0
        )
        style.map("TButton", background=[("active", "#dce1ed")])
        style.configure(
            "Accent.TButton", padding=(14, 9), background=ACCENT,
            foreground="#ffffff", borderwidth=0
        )
        style.map("Accent.TButton", background=[("active", "#4053d4")])
        style.configure(
            "ServiceStart.TButton",
            padding=(14, 9),
            background="#18864b",
            foreground="#ffffff",
            borderwidth=0,
        )
        style.map(
            "ServiceStart.TButton",
            background=[("disabled", "#aab6ad"), ("active", "#126b3b")],
        )
        style.configure(
            "ServiceStop.TButton",
            padding=(14, 9),
            background="#c44455",
            foreground="#ffffff",
            borderwidth=0,
        )
        style.map(
            "ServiceStop.TButton",
            background=[("disabled", "#c5b3b5"), ("active", "#a82f40")],
        )
        style.configure("TCombobox", padding=7, fieldbackground=SURFACE)
        style.configure(
            "TNotebook", background=BACKGROUND, borderwidth=0, tabmargins=(0, 0, 0, 0)
        )
        style.configure(
            "TNotebook.Tab", padding=(16, 10), background="#e9edf5",
            foreground=SUBTLE, borderwidth=0
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", SURFACE)],
            foreground=[("selected", ACCENT)]
        )
        style.configure(
            "Treeview", background=SURFACE, fieldbackground=SURFACE,
            foreground=TEXT, rowheight=48, borderwidth=0
        )
        style.configure(
            "Treeview.Heading", background="#eef1f7", foreground=SUBTLE,
            font=("Segoe UI Semibold", 9), padding=(10, 8), relief="flat"
        )
        style.map(
            "Treeview",
            background=[("selected", "#dbe4ff")],
            foreground=[("selected", TEXT)]
        )

    def _build_layout(self):
        shell = tk.Frame(self.root, background=BACKGROUND)
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, width=216, background=SIDEBAR)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(
            sidebar, text="IB  Circlio", background=SIDEBAR, foreground="#ffffff",
            font=("Segoe UI Semibold", 20), anchor="w"
        ).pack(fill="x", padx=23, pady=(26, 2))
        tk.Label(
            sidebar, text="LOCAL INSTAGRAM HISTORY", background=SIDEBAR,
            foreground="#9aa7c2", font=("Segoe UI", 8, "bold"), anchor="w"
        ).pack(fill="x", padx=25, pady=(0, 26))
        tk.Label(
            sidebar, text="COLLECTIONS AND REPORTS", background=SIDEBAR, foreground="#8390aa",
            font=("Segoe UI", 8, "bold"), anchor="w"
        ).pack(fill="x", padx=24, pady=(0, 8))
        tk.Label(
            sidebar, text="Start collection services,\nthen review local history.",
            background=SIDEBAR, foreground="#bac4d7", justify="left",
            font=("Segoe UI", 9)
        ).pack(side="bottom", anchor="w", padx=24, pady=22)

        content = tk.Frame(shell, background=BACKGROUND)
        content.pack(side="left", fill="both", expand=True)
        topbar = tk.Frame(content, background=BACKGROUND)
        topbar.pack(fill="x", padx=30, pady=(22, 4))
        self.heading = ttk.Label(topbar, text="Your overview", style="Title.TLabel")
        self.heading.pack(side="left")
        ttk.Label(topbar, text="PROFILE", style="Muted.TLabel").pack(side="right", padx=(12, 0))
        self.profile_box = ttk.Combobox(
            topbar, textvariable=self.profile, values=self.profile_values,
            state="readonly", width=22
        )
        self.profile_box.pack(side="right")
        self.profile_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        self.notebook = ttk.Notebook(content)
        self.notebook.pack(fill="both", expand=True, padx=30, pady=(12, 24))
        self.service_tab = ttk.Frame(self.notebook, padding=22)
        self.dashboard_tab = ttk.Frame(self.notebook, padding=22)
        self.snapshot_tab = ttk.Frame(self.notebook, padding=22)
        self.changes_tab = ttk.Frame(self.notebook, padding=22)
        self.browse_tab = ttk.Frame(self.notebook, padding=22)
        self.compare_tab = ttk.Frame(self.notebook, padding=22)
        self.export_tab = ttk.Frame(self.notebook, padding=22)
        self.notebook.add(self.service_tab, text="Collection service")
        self.notebook.add(self.dashboard_tab, text="Overview")
        self.notebook.add(self.snapshot_tab, text="Snapshot report")
        self.notebook.add(self.changes_tab, text="Changes")
        self.notebook.add(self.browse_tab, text="Browse lists")
        self.notebook.add(self.compare_tab, text="Compare")
        self.notebook.add(self.export_tab, text="Export")
        self._build_service()
        self._build_dashboard()
        self._build_snapshot_report()
        self._build_changes()
        self._build_browse()
        self._build_compare()
        self._build_export()

    def _build_service(self):
        tab = self.service_tab
        ttk.Label(tab, text="Instagram collection service", style="Section.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            tab,
            text="Start or stop the local service used by the browser extension. "
            "Keep it running while collecting.",
            style="Muted.TLabel",
            wraplength=850,
        ).pack(anchor="w", pady=(4, 16))

        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=(0, 12))
        ttk.Label(controls, text="Service status:", style="Section.TLabel").pack(
            side="left"
        )
        self.server_status_label = ttk.Label(
            controls, textvariable=self.server_status, style="Muted.TLabel"
        )
        self.server_status_label.pack(side="left", padx=(8, 18))
        self.server_start_button = ttk.Button(
            controls,
            text="Start service",
            style="ServiceStart.TButton",
            command=self._start_server,
        )
        self.server_start_button.pack(side="left")
        self.server_stop_button = ttk.Button(
            controls, text="Stop service", style="ServiceStop.TButton",
            command=self._stop_server, state="disabled"
        )
        self.server_stop_button.pack(side="left", padx=8)
        ttk.Button(
            controls, text="Refresh reports", command=self.refresh
        ).pack(side="right")

        ttk.Label(tab, text="Service log", style="Section.TLabel").pack(
            anchor="w", pady=(4, 8)
        )
        self.server_log_view = scrolledtext.ScrolledText(
            tab,
            height=22,
            wrap="word",
            background="#111827",
            foreground="#d7e0ef",
            insertbackground="#ffffff",
            relief="flat",
            font=("Consolas", 9),
            state="disabled",
        )
        self.server_log_view.pack(fill="both", expand=True)
        self._append_server_log(
            "The local service is stopped. Select Start service before collecting."
        )

    def _append_server_log(self, message):
        if self.server_log_view is None:
            return
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self.server_log_view.configure(state="normal")
        self.server_log_view.insert("end", f"[{timestamp}] {message.rstrip()}\n")
        line_count = int(self.server_log_view.index("end-1c").split(".")[0])
        if line_count > 1000:
            self.server_log_view.delete("1.0", f"{line_count - 1000}.0")
        self.server_log_view.see("end")
        self.server_log_view.configure(state="disabled")

    def _start_server(self):
        if self.server_process and self.server_process.poll() is None:
            return
        token_path = self.data_dir / "server-token.txt"
        config_path = self.app_directory / "config.js"
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            launcher.prepare_local_config(self.data_dir, config_path)
            packaged_server = self.app_directory / "ib-circlio-server.exe"
            if packaged_server.is_file():
                command = [
                    str(packaged_server),
                    "--db", str(self.data_dir / "instagram.db"),
                    "--token-file", str(token_path),
                ]
            else:
                python_executable = Path(sys.executable)
                if python_executable.name.casefold() == "pythonw.exe":
                    console_python = python_executable.with_name("python.exe")
                    if console_python.is_file():
                        python_executable = console_python
                command = [
                    str(python_executable),
                    "-u",
                    str(self.app_directory / "server.py"),
                    "--db", str(self.data_dir / "instagram.db"),
                    "--token-file", str(token_path),
                ]
            self.server_process = subprocess.Popen(
                command,
                cwd=str(self.app_directory),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, ValueError) as error:
            self.server_status.set("Start failed")
            self._append_server_log(f"Could not start the local service: {error}")
            return

        process = self.server_process
        self.server_status.set("Starting")
        self.server_stopping = False
        self.server_start_button.configure(state="disabled")
        self.server_stop_button.configure(state="normal")
        self._append_server_log("Starting the local service...")
        threading.Thread(
            target=self._read_server_output,
            args=(process,),
            daemon=True,
            name="ib-circlio-service-log",
        ).start()

    def _read_server_output(self, process):
        try:
            if process.stdout:
                for line in process.stdout:
                    self.server_messages.put((process, line.rstrip()))
        finally:
            self.server_messages.put((process, None))

    def _stop_server(self):
        process = self.server_process
        if process is None or process.poll() is not None:
            self.server_status.set("Stopped")
            self.server_start_button.configure(state="normal")
            self.server_stop_button.configure(state="disabled")
            return
        self.server_status.set("Stopping")
        self.server_stopping = True
        self.server_stop_button.configure(state="disabled")
        self._append_server_log("Stopping the local service...")
        try:
            process.terminate()
        except OSError as error:
            self.server_status.set("Stop failed")
            self._append_server_log(f"Could not stop the local service: {error}")

    def _poll_server(self):
        while True:
            try:
                process, line = self.server_messages.get_nowait()
            except queue.Empty:
                break
            if process is not self.server_process:
                continue
            if line is None:
                continue
            self._append_server_log(line)
            if "listening on http://" in line:
                self.server_status.set("Running")
        process = self.server_process
        if process is None:
            self.server_start_button.configure(state="normal")
            self.server_stop_button.configure(state="disabled")
        elif process.poll() is not None:
            return_code = process.returncode
            if return_code and not self.server_stopping:
                self.server_status.set(f"Stopped (exit {return_code})")
                self._append_server_log(
                    f"The local service exited with code {return_code}."
                )
            else:
                self.server_status.set("Stopped")
            self.server_start_button.configure(state="normal")
            self.server_stop_button.configure(state="disabled")
            self.server_process = None
            self.server_stopping = False
        self.root.after(200, self._poll_server)
    def _build_dashboard(self):
        tab = self.dashboard_tab
        ttk.Label(tab, text="At a glance", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            tab, text="Your latest complete snapshot and what changed.",
            style="Muted.TLabel"
        ).pack(anchor="w", pady=(2, 18))
        cards = ttk.Frame(tab)
        cards.pack(fill="x", pady=(0, 18))
        self.card_values = {}
        for key, title in (
            ("followers", "FOLLOWERS COLLECTED"),
            ("following", "FOLLOWING COLLECTED"),
            ("new_followers", "NEW FOLLOWERS"),
            ("removed_followers", "LEFT"),
            ("new_following", "NEW FOLLOWING"),
            ("removed_following", "UNFOLLOWED"),
        ):
            card = tk.Frame(cards, bg=SURFACE, highlightbackground="#e7eaf1", highlightthickness=1)
            card.pack(side="left", fill="both", expand=True, padx=(0, 10))
            tk.Label(
                card, text=title, bg=SURFACE, fg=SUBTLE,
                font=("Segoe UI", 8, "bold"), anchor="w"
            ).pack(fill="x", padx=16, pady=(14, 1))
            value = tk.Label(
                card, text="—", bg=SURFACE, fg=TEXT,
                font=("Segoe UI Semibold", 24), anchor="w"
            )
            value.pack(fill="x", padx=16, pady=(0, 13))
            self.card_values[key] = value
        snapshot = ttk.Frame(tab)
        snapshot.pack(fill="x", pady=(0, 16))
        self.latest_label = ttk.Label(snapshot, text="No snapshot loaded", style="Section.TLabel")
        self.latest_label.pack(side="left")
        self.status_label = ttk.Label(snapshot, text="", style="Muted.TLabel")
        self.status_label.pack(side="right")
        self.count_health_label = ttk.Label(tab, text="", style="Muted.TLabel", wraplength=840)
        self.count_health_label.pack(fill="x", anchor="w", pady=(0, 12))
        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=(0, 20))
        ttk.Button(
            buttons, text="View changes", style="Accent.TButton",
            command=lambda: self.notebook.select(self.changes_tab)
        ).pack(side="left")
        ttk.Button(
            buttons, text="Create Excel report",
            command=lambda: self._export_selected(self.latest_collection)
        ).pack(side="left", padx=8)
        ttk.Label(tab, text="Recent snapshots", style="Section.TLabel").pack(anchor="w", pady=(2, 10))
        self.recent_tree = self._tree(
            tab, ("date", "status", "followers", "following"),
            ("Date", "Status", "Followers", "Following"), (220, 150, 130, 130), height=6
        )

    def _build_snapshot_report(self):
        tab = self.snapshot_tab
        ttk.Label(tab, text="Complete collection report", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            tab,
            text="Lists first, followed by changes from the previous complete collection.",
            style="Muted.TLabel"
        ).pack(anchor="w", pady=(3, 12))
        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=(0, 10))
        self.snapshot_report_date = tk.StringVar()
        self.snapshot_report_box = ttk.Combobox(
            controls, textvariable=self.snapshot_report_date, state="readonly", width=20
        )
        self.snapshot_report_box.pack(side="left")
        ttk.Button(
            controls, text="Load snapshot report", style="Accent.TButton",
            command=self._load_snapshot_report
        ).pack(side="left", padx=10)
        self.snapshot_timestamp = ttk.Label(tab, text="Choose a snapshot.", style="Section.TLabel")
        self.snapshot_timestamp.pack(anchor="w", pady=(0, 4))
        self.snapshot_counts = ttk.Label(tab, text="", style="Muted.TLabel")
        self.snapshot_counts.pack(anchor="w", pady=(0, 12))
        self.snapshot_tree = self._tree(
            tab, ("username", "details"),
            ("Username", "Details"), (260, 450), height=11, avatars=True
        )
        self.snapshot_tree.tag_configure("section", background="#eef1f7", foreground=TEXT)
        self.snapshot_tree.tag_configure("added", foreground=GREEN)
        self.snapshot_tree.tag_configure("removed", foreground=RED)

    def _build_changes(self):
        ttk.Label(self.changes_tab, text="Changed accounts", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            self.changes_tab, text="Changes from the previous complete snapshot.",
            style="Muted.TLabel"
        ).pack(anchor="w", pady=(3, 16))
        self.changes_empty_label = ttk.Label(
            self.changes_tab,
            text="",
            style="Muted.TLabel",
        )
        self.changes_empty_label.pack(anchor="w", pady=(0, 8))
        self.changes_tree = self._tree(
            self.changes_tab, ("relationship", "change", "username"),
            ("List", "Change", "Username"), (180, 180, 480), height=15,
            avatars=True
        )

    def _build_browse(self):
        ttk.Label(self.browse_tab, text="Browse a saved list", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            self.browse_tab, text="Choose a snapshot and relationship to see every saved username.",
            style="Muted.TLabel"
        ).pack(anchor="w", pady=(3, 16))
        controls = ttk.Frame(self.browse_tab)
        controls.pack(fill="x", pady=(0, 14))
        self.browse_date = tk.StringVar()
        self.browse_relationship = tk.StringVar(value="Followers")
        self.browse_membership_filter = tk.StringVar(value="Everyone")
        ttk.Label(controls, text="Snapshot").pack(side="left", padx=(0, 8))
        self.browse_date_box = ttk.Combobox(
            controls, textvariable=self.browse_date, state="readonly", width=31
        )
        self.browse_date_box.pack(side="left")
        ttk.Label(controls, text="List").pack(side="left", padx=(18, 8))
        self.browse_relationship_box = ttk.Combobox(
            controls, textvariable=self.browse_relationship, state="readonly",
            values=("Followers", "Following", "Both"), width=14
        )
        self.browse_relationship_box.pack(side="left")
        ttk.Label(controls, text="Show").pack(side="left", padx=(18, 8))
        self.browse_filter_box = ttk.Combobox(
            controls,
            textvariable=self.browse_membership_filter,
            state="disabled",
            values=(
                "Everyone",
                "Follows profile only",
                "Profile follows only",
                "Mutual follows",
            ),
            width=22,
        )
        self.browse_filter_box.pack(side="left")
        ttk.Button(controls, text="Show list", style="Accent.TButton", command=self._load_browse).pack(
            side="left", padx=8
        )
        ttk.Label(
            self.browse_tab,
            text="Follows profile only = in Followers, not Following. "
            "Profile follows only = in Following, not Followers.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 10))
        self.browse_relationship_box.bind(
            "<<ComboboxSelected>>", self._browse_relationship_changed
        )
        self.browse_filter_box.bind(
            "<<ComboboxSelected>>", lambda _event: self._load_browse()
        )
        self.browse_tree = self._tree(
            self.browse_tab, ("membership", "username"),
            ("Relationship", "Username"), (220, 300), height=13, avatars=True
        )

    def _build_compare(self):
        ttk.Label(self.compare_tab, text="Compare snapshots", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            self.compare_tab, text="Find accounts added or removed between two complete collections.",
            style="Muted.TLabel"
        ).pack(anchor="w", pady=(3, 16))
        controls = ttk.Frame(self.compare_tab)
        controls.pack(fill="x", pady=(0, 14))
        self.from_date = tk.StringVar()
        self.to_date = tk.StringVar()
        self.compare_relationship = tk.StringVar(value="Both")
        ttk.Label(controls, text="From").pack(side="left", padx=(0, 8))
        self.from_box = ttk.Combobox(controls, textvariable=self.from_date, state="readonly", width=16)
        self.from_box.configure(width=31)
        self.from_box.pack(side="left")
        ttk.Label(controls, text="To").pack(side="left", padx=(16, 8))
        self.to_box = ttk.Combobox(controls, textvariable=self.to_date, state="readonly", width=16)
        self.to_box.configure(width=31)
        self.to_box.pack(side="left")
        ttk.Combobox(
            controls, textvariable=self.compare_relationship, state="readonly",
            values=("Both", "Followers", "Following"), width=14
        ).pack(side="left", padx=(16, 0))
        ttk.Button(controls, text="Compare", style="Accent.TButton", command=self._load_comparison).pack(
            side="left", padx=12
        )
        self.compare_tree = self._tree(
            self.compare_tab, ("timestamp", "relationship", "change", "username"),
            ("Changed at", "List", "Change", "Username"),
            (230, 150, 130, 280), height=13,
            avatars=True
        )
        self.compare_summary = ttk.Label(
            self.compare_tab, text="Choose two snapshots and select Compare.",
            style="Muted.TLabel"
        )
        self.compare_summary.pack(anchor="w", pady=(10, 0))

    def _build_export(self):
        ttk.Label(self.export_tab, text="Export a report", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            self.export_tab,
            text="Create a formatted Excel workbook for any complete snapshot.",
            style="Muted.TLabel"
        ).pack(anchor="w", pady=(3, 20))
        panel = tk.Frame(self.export_tab, bg=SURFACE, highlightbackground="#e7eaf1", highlightthickness=1)
        panel.pack(fill="x")
        ttk.Label(panel, text="Snapshot date", style="Section.TLabel").pack(
            anchor="w", padx=20, pady=(18, 7)
        )
        self.export_date = tk.StringVar()
        self.export_date_box = ttk.Combobox(
            panel, textvariable=self.export_date, state="readonly", width=22
        )
        self.export_date_box.pack(anchor="w", padx=20)
        ttk.Label(
            panel,
            text="The workbook contains a summary and account changes from the previous complete snapshot.",
            style="Muted.TLabel", wraplength=680
        ).pack(anchor="w", padx=20, pady=12)
        ttk.Button(
            panel, text="Choose location and export Excel", style="Accent.TButton",
            command=self._export_chosen_snapshot
        ).pack(anchor="w", padx=20, pady=(0, 20))

    def _tree(self, parent, columns, headings, widths, height=10, avatars=False):
        table_toolbar = ttk.Frame(parent)
        table_toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(table_toolbar, text="Search username").pack(side="left", padx=(0, 8))
        search_var = tk.StringVar()
        ttk.Entry(table_toolbar, textvariable=search_var, width=28).pack(side="left")
        status = ttk.Label(table_toolbar, text="", style="Muted.TLabel")
        status.pack(side="left", padx=8)
        wrap = ttk.Frame(parent)
        wrap.pack(fill="both", expand=True)
        tree = ttk.Treeview(
            wrap, columns=columns,
            show="tree headings" if avatars else "headings",
            height=height
        )
        vertical = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        if avatars:
            tree.heading("#0", text="Avatar", anchor="w")
            tree.column("#0", width=64, minwidth=64, stretch=False, anchor="w")
        for column, heading, width in zip(columns, headings, widths):
            tree.heading(column, text=heading, anchor="w")
            tree.column(column, width=width, minwidth=90, anchor="w", stretch=False)
        self.tree_headers[tree] = tuple(headings)
        self.tree_items[tree] = []
        self.tree_search_vars[tree] = search_var
        self.tree_search_status[tree] = status
        search_var.trace_add(
            "write", lambda *_args, table=tree: self._filter_tree(table)
        )
        ttk.Button(
            table_toolbar, text="Export table", image=self.excel_icon,
            compound="left",
            command=lambda table=tree: self._export_tree(table)
        ).pack(side="right")
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        tree.tag_configure("added", foreground=GREEN)
        tree.tag_configure("removed", foreground=RED)
        tree.tag_configure("both", background="#eee8ff", foreground="#5935a6")
        tree.tag_configure("followers-only", background="#e4f1ff", foreground="#205b9d")
        tree.tag_configure("following-only", background="#e4f7ec", foreground="#187647")
        tree.bind("<Double-1>", lambda event, table=tree: self._open_avatar(event, table))
        return tree

    def _make_excel_icon(self):
        image = Image.new("RGB", (24, 24), "#18864b")
        draw = ImageDraw.Draw(image)
        draw.rectangle((3, 2, 20, 22), fill="#ffffff", outline="#14743f")
        draw.rectangle((3, 2, 9, 22), fill="#14743f")
        draw.text((5, 7), "X", fill="#ffffff")
        draw.text((11, 6), "=", fill="#18864b")
        draw.text((11, 12), "=", fill="#18864b")
        return ImageTk.PhotoImage(image, master=self.root)

    def _insert_avatar_row(self, tree, avatar_path=None, *, values, tags=()):
        item = tree.insert(
            "", "end",
            image=self._avatar_photo(avatar_path),
            values=values,
            tags=tags,
        )
        if avatar_path:
            self.avatar_paths_by_item[(tree, item)] = avatar_path
        return item

    def _clear_tree(self, tree):
        item_ids = set(self.tree_items.get(tree, ())) | set(tree.get_children())
        for item in item_ids:
            self.avatar_paths_by_item.pop((tree, item), None)
            tree.delete(item)
        self.tree_items[tree] = []

    def _refresh_tree_search(self, tree):
        self.tree_items[tree] = list(tree.get_children())
        self._filter_tree(tree)

    def _filter_tree(self, tree):
        rows = [
            (item, tree.item(item, "values"))
            for item in self.tree_items.get(tree, ())
            if tree.exists(item)
        ]
        matching = matching_tree_items(rows, self.tree_search_vars[tree].get())
        for item, _values in rows:
            if item in matching:
                tree.move(item, "", "end")
            else:
                tree.detach(item)
        visible = len(matching)
        total = len(rows)
        self.tree_search_status[tree].configure(
            text=f"{visible:,} of {total:,}" if visible != total else f"{total:,} rows"
        )

    def _open_avatar(self, event, tree):
        if tree.identify_region(event.x, event.y) != "tree":
            return
        item = tree.identify_row(event.y)
        image_path = self.avatar_paths_by_item.get((tree, item))
        if not image_path or not Path(image_path).is_file():
            return
        try:
            with Image.open(image_path) as source:
                image = ImageOps.contain(source.convert("RGB"), (560, 560))
            photo = ImageTk.PhotoImage(image)
        except (OSError, UnidentifiedImageError) as error:
            return self._error(f"Could not open this profile image:\n{error}")
        window = tk.Toplevel(self.root)
        window.title("Profile picture")
        window.configure(background=SURFACE)
        window.transient(self.root)
        window.resizable(False, False)
        label = ttk.Label(window, image=photo)
        label.image = photo
        label.pack(padx=18, pady=18)
        window.bind("<Escape>", lambda _event: window.destroy())
        window.bind("<Destroy>", self._avatar_window_closed, add="+")
        self.avatar_window = window

    def _avatar_window_closed(self, event):
        if event.widget is self.avatar_window:
            self.avatar_window = None

    def _close_avatar_on_app_click(self, event):
        if not self.avatar_window:
            return
        try:
            if event.widget.winfo_toplevel() is self.root:
                self.avatar_window.destroy()
                self.root.lift()
        except tk.TclError:
            self.avatar_window = None

    def _export_tree(self, tree):
        rows = []
        for item in tree.get_children():
            if "section" in tree.item(item, "tags"):
                continue
            values = tree.item(item, "values")
            if values:
                rows.append(values)
        if not rows:
            return self._error("There are no rows in this table to export.")
        headers = self.tree_headers[tree]
        default_name = (
            f"ib-circlio_{self.profile.get()}_table_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        )
        output = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export this table",
            initialfile=default_name,
            defaultextension=".xlsx",
            filetypes=(("Excel workbook", "*.xlsx"),),
        )
        if not output:
            return
        try:
            result.write_table_workbook(Path(output), "IB_Circlio", headers, rows)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            return self._error(f"Could not export this table:\n{error}")
        messagebox.showinfo("Table exported", f"Saved Excel table to:\n{output}", parent=self.root)

    def refresh(self):
        self.profile_values = self._profiles()
        self.profile_box.configure(values=self.profile_values)
        profile = self.profile.get()
        if not profile:
            if not self.profile_values:
                self.snapshots = []
                self.snapshot_choices = []
                self.dates = []
                self._fill_snapshots()
                self._refresh_dashboard()
                self._refresh_changes()
                self._append_server_log(
                    "No saved profiles yet. Collect a snapshot, then select Refresh reports."
                )
                return
            self.profile.set(self.profile_values[0])
            profile = self.profile.get()
        elif profile not in self.profile_values:
            self.profile.set(self.profile_values[0] if self.profile_values else "")
            profile = self.profile.get()
        if not profile:
            return
        self.snapshot_choices = snapshot_choices(self.connection, profile)
        self.snapshots = [
            {"id": collection_id, "captured_at": label}
            for label, collection_id in self.snapshot_choices
        ]
        self.dates = result.snapshot_dates(self.connection, profile)
        self._fill_snapshots()
        self._refresh_dashboard()
        self._refresh_changes()
        if self.snapshots:
            self._load_snapshot_report()

    def _fill_snapshots(self):
        self.snapshot_ids = {
            label: collection_id for label, collection_id in self.snapshot_choices
        }
        values = list(self.snapshot_ids)
        for box in (
            self.browse_date_box, self.snapshot_report_box,
            self.from_box, self.to_box, self.export_date_box
        ):
            box.configure(values=values)
        if not values:
            return
        variables = (
            self.browse_date, self.snapshot_report_date, self.export_date, self.to_date
        )
        for variable in variables:
            if variable.get() not in values:
                variable.set(values[-1])
        if self.from_date.get() not in values:
            self.from_date.set(values[-2] if len(values) > 1 else values[-1])

    def _refresh_dashboard(self):
        self._clear_tree(self.recent_tree)
        self.latest_collection = result.latest_collection(self.connection, self.profile.get())
        collection = self.latest_collection
        if not collection:
            self.latest_label.configure(text="No complete snapshots yet")
            self.status_label.configure(text="Collect a snapshot with the extension first.")
            for value in self.card_values.values():
                value.configure(text="—")
            self._refresh_tree_search(self.recent_tree)
            return
        self.latest_label.configure(text=f"Latest snapshot  ·  {format_timestamp(collection[1])}")
        self.status_label.configure(text="Complete")
        self.card_values["followers"].configure(
            text=f"{result.count_members(self.connection, collection[0], 'followers'):,}"
        )
        self.card_values["following"].configure(
            text=f"{result.count_members(self.connection, collection[0], 'following'):,}"
        )
        previous = result.previous_collection(self.connection, self.profile.get(), collection[0])
        changes = result.grouped_changes(self.connection, previous, collection)
        self.card_values["new_followers"].configure(text=f"{len(changes['followers']['added']):,}")
        self.card_values["removed_followers"].configure(text=f"{len(changes['followers']['removed']):,}")
        self.card_values["new_following"].configure(text=f"{len(changes['following']['added']):,}")
        self.card_values["removed_following"].configure(text=f"{len(changes['following']['removed']):,}")
        self.count_health_label.configure(
            text=self._count_health_text(collection),
            foreground=RED if self._counts_mismatch(collection) else SUBTLE
        )
        for snapshot in reversed(self.snapshots[-8:]):
            item = result.collection_for_date(
                self.connection, self.profile.get(), str(snapshot["id"])
            )
            self.recent_tree.insert("", "end", values=(
                format_timestamp(item[1]), "Complete",
                f"{result.count_members(self.connection, item[0], 'followers'):,}",
                f"{result.count_members(self.connection, item[0], 'following'):,}",
            ))
        self._refresh_tree_search(self.recent_tree)

    def _refresh_changes(self):
        self._clear_tree(self.changes_tree)
        collection = result.latest_collection(self.connection, self.profile.get())
        if not collection:
            self.changes_empty_label.configure(
                text="Collect a complete snapshot to see changes here."
            )
            self._refresh_tree_search(self.changes_tree)
            return
        previous = result.previous_collection(self.connection, self.profile.get(), collection[0])
        if not previous:
            self.changes_empty_label.configure(
                text="A second complete snapshot is needed to compare."
            )
            self._refresh_tree_search(self.changes_tree)
            return
        self.changes_empty_label.configure(text="")
        changes = result.grouped_changes(self.connection, previous, collection)
        for relationship in result.RELATIONSHIPS:
            for change in ("added", "removed"):
                for username in changes[relationship][change]:
                    avatar_collection_id = (
                        collection[0] if change == "added" else previous[0]
                    )
                    self._insert_avatar_row(
                        self.changes_tree,
                        self._avatar_path(avatar_collection_id, username),
                        values=(relationship.title(), change.title(), username),
                        tags=(change,),
                    )
        self._refresh_tree_search(self.changes_tree)

    def _counts_mismatch(self, collection):
        return any(item["difference"] for item in collection_count_audit(
            self.connection, collection
        ))

    def _count_health_text(self, collection):
        parts = []
        for item in collection_count_audit(self.connection, collection):
            title = item["relationship"].title()
            collected = item["collected"]
            if item["invalid_reason"]:
                parts.append(
                    f"{title}: Instagram total unverified; {collected:,} distinct usernames collected."
                )
            elif item["shown"] is None:
                parts.append(
                    f"{title}: Instagram total unavailable; {collected:,} distinct usernames collected."
                )
        return "     •     ".join(parts) if parts else "Instagram totals match collected usernames."

    def _load_snapshot_report(self):
        selected_snapshot = self.snapshot_report_date.get()
        if selected_snapshot not in self.snapshot_ids:
            return self._error("Choose a complete snapshot first.")
        collection = result.collection_for_date(
            self.connection, self.profile.get(), str(self.snapshot_ids[selected_snapshot])
        )
        previous = result.previous_collection(
            self.connection, self.profile.get(), collection[0]
        )
        self._clear_tree(self.snapshot_tree)
        self.snapshot_timestamp.configure(
            text=f"Collection timestamp  ·  {collection[1]}"
        )
        count_summary = []
        for item in collection_count_audit(self.connection, collection):
            label = item["relationship"].title()
            collected = item["collected"]
            shown_text = (
                "unverified" if item["invalid_reason"]
                else "unavailable" if item["shown"] is None
                else f"{item['shown']:,} (verified)"
            )
            count_summary.append(
                f"{label}: {collected:,} collected · Instagram total {shown_text}"
            )
        coverage = collection_avatar_coverage(self.connection, collection[0])
        count_summary.append(
            f"Profile images: {coverage['saved']:,}/{coverage['total']:,} saved"
        )
        self.snapshot_counts.configure(text="     •     ".join(count_summary))

        for relationship in result.RELATIONSHIPS:
            self.snapshot_tree.insert(
                "", "end",
                values=(relationship.title().upper(), ""),
                tags=("section",)
            )
            for username, avatar_path in result._members(
                self.connection, collection[0], relationship
            ):
                self._insert_avatar_row(
                    self.snapshot_tree, avatar_path,
                    values=(username, relationship.title()),
                )

        self.snapshot_tree.insert(
            "", "end",
            values=("CHANGES FROM PREVIOUS", ""),
            tags=("section",)
        )
        if previous:
            changes = result.grouped_changes(self.connection, previous, collection)
            for relationship in result.RELATIONSHIPS:
                for change in ("added", "removed"):
                    for username in changes[relationship][change]:
                        self._insert_avatar_row(
                            self.snapshot_tree,
                            self._avatar_path(
                                collection[0] if change == "added" else previous[0],
                                username
                            ),
                            values=(
                                username,
                                f"{relationship.title()} · {change.title()} · "
                                f"{previous[1]} → {collection[1]}"
                            ),
                            tags=(change,)
                        )
        else:
            self.snapshot_tree.insert(
                "", "end",
                values=("No earlier complete snapshot", "Nothing to compare yet.")
            )
        self._refresh_tree_search(self.snapshot_tree)

    def _load_browse(self):
        snapshot = self.browse_date.get()
        if snapshot not in self.snapshot_ids:
            return self._error("No complete snapshot is available.")
        collection = result.collection_for_date(
            self.connection, self.profile.get(), str(self.snapshot_ids[snapshot])
        )
        choice = self.browse_relationship.get().lower()
        self._clear_tree(self.browse_tree)
        rows = browse_members(
            self.connection, collection[0], choice
        )
        if choice == "both":
            rows = filter_browse_members(
                rows, self.browse_membership_filter.get()
            )
        for marker, username, avatar_path in rows:
            color_tag = {
                "[=] Both": "both",
                "[F] Followers only": "followers-only",
                "[>] Following only": "following-only",
            }.get(marker)
            self._insert_avatar_row(
                self.browse_tree, avatar_path,
                values=(marker, username),
                tags=(color_tag,) if color_tag else (),
            )
        self._refresh_tree_search(self.browse_tree)

    def _browse_relationship_changed(self, _event=None):
        is_combined = self.browse_relationship.get().lower() == "both"
        self.browse_filter_box.configure(
            state="readonly" if is_combined else "disabled"
        )
        if self.browse_date.get() in self.snapshot_ids:
            self._load_browse()

    def _load_comparison(self):
        if self.from_date.get() not in self.snapshot_ids or self.to_date.get() not in self.snapshot_ids:
            return self._error("Choose both snapshot dates first.")
        selected = self.compare_relationship.get().lower()
        relationships = result.RELATIONSHIPS if selected == "both" else (selected,)
        from_id = self.snapshot_ids[self.from_date.get()]
        to_id = self.snapshot_ids[self.to_date.get()]
        try:
            before, after, differences = comparison_timeline(
                self.connection, self.profile.get(), from_id, to_id, relationships
            )
        except ValueError as error:
            return self._error(str(error))
        self._clear_tree(self.compare_tree)
        if differences:
            added = sum(event[3] == "Added" for event in differences)
            removed = sum(event[3] == "Removed" for event in differences)
            self.compare_summary.configure(
                text=(
                    f"{len(differences):,} membership changes across snapshots · "
                    f"{added:,} added · {removed:,} removed"
                )
            )
        else:
            selected_label = self.compare_relationship.get()
            self.compare_summary.configure(
                text=(
                    f"No membership changes in {selected_label} between these snapshots."
                )
            )
        for timestamp, collection_id, relationship, change, username in differences:
            self._insert_avatar_row(
                self.compare_tree,
                self._avatar_path(collection_id, username),
                values=(timestamp, relationship, change, username),
                tags=(change.lower(),),
            )
        if not differences:
            self.compare_tree.insert(
                "", "end", values=("", "", "", "No changes for this selection.")
            )
        self._refresh_tree_search(self.compare_tree)
        self.root.title(
            f"IB_Circlio | {format_timestamp(before[1])} → {format_timestamp(after[1])}"
        )

    def _avatar_path(self, collection_id, username):
        row = self.connection.execute(
            """SELECT cav.image_path FROM collection_avatar_versions cav
               JOIN users u ON u.id = cav.user_id
               JOIN collections image_collection ON image_collection.id = cav.collection_id
               JOIN collections target ON target.id = ?
               WHERE image_collection.profile_id = target.profile_id
                 AND image_collection.complete = 1
                 AND image_collection.captured_at <= target.captured_at
                 AND u.username = ?
               ORDER BY image_collection.captured_at DESC, image_collection.id DESC
               LIMIT 1""",
            (collection_id, username),
        ).fetchone()
        if row:
            return row[0]
        row = self.connection.execute(
            "SELECT avatar_path FROM users WHERE username = ?", (username,)
        ).fetchone()
        return row[0] if row else None

    def _avatar_photo(self, image_path):
        if image_path and image_path in self.avatar_photos:
            return self.avatar_photos[image_path]
        if image_path and image_path not in self.avatar_errors:
            try:
                with Image.open(image_path) as source:
                    image = ImageOps.fit(source.convert("RGB"), (36, 36))
                photo = ImageTk.PhotoImage(image, master=self.root)
                self.avatar_photos[image_path] = photo
                return photo
            except (OSError, UnidentifiedImageError, tk.TclError):
                self.avatar_errors.add(image_path)
        return self._placeholder_photo()

    def _placeholder_photo(self):
        if self.avatar_placeholder is None:
            image = Image.new("RGB", (36, 36), "#e5e9f2")
            draw = ImageDraw.Draw(image)
            draw.ellipse((12, 5, 24, 17), fill="#98a3b6")
            draw.ellipse((6, 19, 30, 40), fill="#98a3b6")
            self.avatar_placeholder = ImageTk.PhotoImage(image, master=self.root)
        return self.avatar_placeholder

    def _selected_export_collection(self):
        snapshot = self.export_date.get()
        if snapshot not in self.snapshot_ids:
            raise ValueError("No complete snapshot is available to export.")
        return result.collection_for_date(
            self.connection, self.profile.get(), str(self.snapshot_ids[snapshot])
        )

    def _export_chosen_snapshot(self):
        try:
            collection = self._selected_export_collection()
        except ValueError as error:
            return self._error(str(error))
        self._export_selected(collection)

    def _export_selected(self, collection):
        if not collection:
            return self._error("No complete snapshot is available to export.")
        default_name = (
            f"ib-circlio_{self.profile.get()}_{collection[1][:10]}.xlsx"
        )
        output = filedialog.asksaveasfilename(
            parent=self.root, title="Save Excel report", initialfile=default_name,
            defaultextension=".xlsx", filetypes=(("Excel workbook", "*.xlsx"),)
        )
        if not output:
            return
        previous = result.previous_collection(
            self.connection, self.profile.get(), collection[0]
        )
        grouped = result.grouped_changes(self.connection, previous, collection)
        try:
            result.write_workbook(
                Path(output), self.profile.get(), collection, grouped,
                self.connection, result.avatar_changes(
                    self.connection, self.profile.get(), collection[0]
                )
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            return self._error(f"Could not create the workbook:\n{error}")
        messagebox.showinfo("Excel report created", f"Saved report to:\n{output}", parent=self.root)

    def _error(self, message):
        messagebox.showerror("IB_Circlio", message, parent=self.root)

    def close(self):
        process = self.server_process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self.connection.close()
        self.root.destroy()


def format_timestamp(value):
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%b %d, %Y  ·  %H:%M")
    except (TypeError, ValueError):
        return value


def main():
    parser = argparse.ArgumentParser(description="IB_Circlio desktop application")
    parser.add_argument(
        "--data-dir",
        default=str(Path.home() / "Desktop" / "Instagram Exporter Data"),
    )
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser()
    database_path = data_dir / "instagram.db"
    root = tk.Tk()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        database = server.Database(str(database_path))
        database.close()
        connection = sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        app = CirclioReportApp(root, connection, data_dir)
        if not app.profile_values:
            messagebox.showinfo(
                "Welcome to IB_Circlio",
                "Start the collection service here, then collect a snapshot "
                "from the browser extension.",
                parent=root,
            )
        root.mainloop()
    except (OSError, sqlite3.Error, tk.TclError, ValueError) as error:
        messagebox.showerror("IB_Circlio could not start", str(error), parent=root)
        root.destroy()


if __name__ == "__main__":
    main()
