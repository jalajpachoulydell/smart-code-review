# app.py
import json
import os
import re
import uuid
import datetime
import webbrowser
import urllib.parse
import tkinter as tk
from tkinter import Tk, StringVar, BOTH, LEFT, RIGHT, X, TOP, BOTTOM, NSEW, messagebox
from tkinter import ttk
from tkinter import filedialog
from dotenv import load_dotenv
import requests
import certifi

from .config import (
    DEFAULT_CONFIG, config_path_for_correlation, load_last_config_path,
    save_last_config_path, load_config, save_config,
)
from .storage import STORE_DIR, ensure_store_dir, load_index, save_index
from .tls import patch_certifi_with_pki_zip
from .github_api import parse_pr_url, fetch_pr_meta, fetch_pr_diff_filtered, fetch_all_prs
from .review_engine import single_model_review
from .html_utils import wrap_fragment_as_full_html, human_repo
from .model_registry import MODEL_REGISTRY
from .file_history_tab import FileHistoryTab
from typing import Optional


# ---------------------- Helpers: GitHub repos for owner ----------------------
def github_api_base_from_host(host: str) -> str:
    return "https://api.github.com" if host.lower() == "github.com" else f"https://{host}/api/v3"



def fetch_all_repos_for_owner(cfg: dict, host: str, owner: str):
    import json
    token = (cfg.get("github_token") or "").strip()
    if not token:
        raise RuntimeError("Missing GitHub PAT in Configuration.")

    api_base = github_api_base_from_host(host)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "pr-reviewer-ui",
    }

    def paged(url_base):
        out, page, per_page = [], 1, 100
        while True:
            url = f"{url_base}?per_page={per_page}&page={page}"
            r = requests.get(url, headers=headers, verify=certifi.where(), timeout=60)
            if r.status_code == 404:
                return None
            if not r.ok:
                raise RuntimeError(f"Failed to fetch repos: {r.status_code} {r.text}")
            batch = r.json() or []
            if not batch:
                break
            out.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return out

    # Define cache file path
    cache_file = os.path.join(STORE_DIR, "repo_cache", f"{owner}_repos.json")

    # Try loading from cache
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, list):
                cached_names = sorted({r.get("name", "") for r in cached if r.get("name")}, key=str.lower)
            else:
                cached_names = []
        except Exception:
            cached_names = []
    else:
        cached_names = []

    # Fetch fresh data
    repos = paged(f"{api_base}/user/repos") or []
    org_repos = paged(f"{api_base}/orgs/{owner}/repos") or []
    repos.extend(org_repos)

    if not repos:
        if cached_names:
            return cached_names
        raise RuntimeError(f"Owner not found or no accessible repos for: {owner}")

    # Update cache
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(repos, f, indent=2)
    except Exception:
        pass

    return sorted({r.get("name", "") for r in repos if r.get("name")}, key=str.lower)



# ---------------------- UI: Autocomplete Combobox for repos ----------------------
class AutoCompleteCombobox(ttk.Combobox):
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self._all_values = []
        self._var = kwargs.get("textvariable") or tk.StringVar()
        self.configure(textvariable=self._var)
        self.bind("<KeyRelease>", self._on_keyrelease)
        self.bind("<<ComboboxSelected>>", self._on_select)

    def set_completion_list(self, values):
        unique_values = sorted(set(values or []), key=str.lower)
        self._all_values = unique_values
        self["values"] = self._all_values

    def _on_keyrelease(self, _):
        text = (self.get() or "").lower()
        if not text:
            self["values"] = self._all_values
            return
        filt = [v for v in self._all_values if text in v.lower()]
        self["values"] = filt

    def _on_select(self, _):
        self["values"] = self._all_values


# ---------------------- Main App ----------------------
class App(Tk):
    def __init__(self):
        super().__init__()
        load_dotenv(".env", override=True)
        self.title("PR Reviewer (DELL)")
        self.geometry("1180x880")
        self.minsize(1080, 840)

        # Load config
        last_cfg = load_last_config_path()
        if last_cfg:
            self.config_path = last_cfg
            self.cfg = load_config(last_cfg)
        else:
            self.config_path = config_path_for_correlation(DEFAULT_CONFIG["correlation_id"])
            self.cfg = load_config(self.config_path)
            save_last_config_path(self.config_path)

        ensure_store_dir()

        # state used across tabs
        self.last_host = "github.com"
        self.last_owner = ""
        self.last_repo = ""
        self.closed_pr_items = []  # store raw PRs for filtering (all states)

        # Notebook
        self.nb = ttk.Notebook(self)
        self.tab_config = ttk.Frame(self.nb)
        self.tab_pr = ttk.Frame(self.nb)
        self.tab_history = ttk.Frame(self.nb)
        self.nb.add(self.tab_config, text="Configuration")
        self.nb.add(self.tab_pr, text="Pull Requests")
        self.nb.add(self.tab_history, text="Code Review History")
        self.nb.pack(expand=True, fill=BOTH)

        # Build tabs
        self._build_tab_configuration()
        self._build_tab_pr()
        self._build_tab_history()

        # File History tab (separate file/class)
        self.file_history_tab = FileHistoryTab(self, self.nb)

        # Feedback tab (last)
        self.tab_feedback = ttk.Frame(self.nb)
        self.nb.add(self.tab_feedback, text="Feedback")
        self._build_tab_feedback()

        self.nb.pack(expand=True, fill=BOTH)
        self._build_status()

        # Apply config to UI now that widgets exist
        self._apply_cfg_to_ui()
        self.render_history()

    # ---------------------- Feedback Tab ----------------------
    def _build_tab_feedback(self):
        outer = ttk.LabelFrame(self.tab_feedback, text="Send Feedback")
        outer.pack(side=TOP, fill=BOTH, expand=True, padx=10, pady=10)

        # To (fixed, disabled)
        to_row = ttk.Frame(outer); to_row.pack(fill=X, padx=6, pady=(6, 2))
        ttk.Label(to_row, text="To:").pack(side=LEFT)
        self.feedback_to = tk.StringVar(value="jalaj.pachouly@dell.com")
        ttk.Entry(to_row, textvariable=self.feedback_to, width=46, state="disabled").pack(side=LEFT, padx=6)

        # Subject
        subj_row = ttk.Frame(outer); subj_row.pack(fill=X, padx=6, pady=2)
        ttk.Label(subj_row, text="Subject:").pack(side=LEFT)
        default_subject = f"PR Reviewer Feedback — Profile: {self.cfg.get('correlation_id', 'default')}"
        self.feedback_subject = tk.StringVar(value=default_subject)
        ttk.Entry(subj_row, textvariable=self.feedback_subject).pack(side=LEFT, padx=6, fill=X, expand=True)

        # Body
        body_row = ttk.Frame(outer); body_row.pack(fill=BOTH, expand=True, padx=6, pady=6)
        ttk.Label(body_row, text="Message:").pack(side=TOP, anchor="w")
        self.feedback_text = tk.Text(body_row, height=14, wrap="word")
        self.feedback_text.pack(side=TOP, fill=BOTH, expand=True, pady=(4, 0))

        # Actions
        btn_row = ttk.Frame(outer); btn_row.pack(fill=X, padx=6, pady=8)
        ttk.Button(btn_row, text="Open Outlook / Mail", command=self._open_feedback_mailto).pack(side=LEFT)
        ttk.Button(btn_row, text="Clear", command=lambda: self.feedback_text.delete("1.0", "end")).pack(side=LEFT, padx=6)

    def _open_feedback_mailto(self):
        to_addr = "jalaj.pachouly@dell.com"
        subject = self.feedback_subject.get() or "PR Reviewer Feedback"
        body = self.feedback_text.get("1.0", "end").strip()
        params = []
        if subject:
            params.append("subject=" + urllib.parse.quote(subject))
        if body:
            params.append("body=" + urllib.parse.quote(body))
        query = "&".join(params)
        uri = f"mailto:{to_addr}" + (f"?{query}" if query else "")
        try:
            webbrowser.open(uri)
            self.set_status("Opening your mail client…")
        except Exception as e:
            messagebox.showerror("Feedback", f"Could not open mail client.\n{e}")

    # ---------------------- Configuration Tab ----------------------
    def _build_tab_configuration(self):
        settings = ttk.LabelFrame(self.tab_config, text="Profile & Credentials")
        settings.pack(side=TOP, fill=X, padx=10, pady=10)
        grid = ttk.Frame(settings); grid.pack(fill=X, padx=6, pady=6)

        # Row 1: Profile Name (Correlation ID)
        ttk.Label(grid, text="Profile Name").grid(row=0, column=0, sticky="w")
        self.v_corr = StringVar()
        ttk.Entry(grid, textvariable=self.v_corr).grid(row=1, column=0, sticky="we", padx=6, pady=4)

        # Row 1b: GitHub PAT
        ttk.Label(grid, text="GitHub Token (PAT)").grid(row=0, column=1, sticky="w")
        self.v_gh = StringVar()
        ttk.Entry(grid, textvariable=self.v_gh, show="*").grid(row=1, column=1, sticky="we", padx=6, pady=4)

        # Row 2: Client ID
        ttk.Label(grid, text="Client ID").grid(row=2, column=0, sticky="w")
        self.v_cid = StringVar()
        ttk.Entry(grid, textvariable=self.v_cid).grid(row=3, column=0, sticky="we", padx=6, pady=4)

        # Row 2b: Client Secret
        ttk.Label(grid, text="Client Secret").grid(row=2, column=1, sticky="w")
        self.v_csec = StringVar()
        ttk.Entry(grid, textvariable=self.v_csec, show="*").grid(row=3, column=1, sticky="we", padx=6, pady=4)

        # Row 3: Host
        ttk.Label(grid, text="Host Name").grid(row=4, column=0, sticky="w")
        self.v_host = StringVar()
        ttk.Entry(grid, textvariable=self.v_host).grid(row=5, column=0, sticky="we", padx=6, pady=4)

        # Row 2b: Org
        ttk.Label(grid, text="Org").grid(row=4, column=1, sticky="w")
        self.v_org = StringVar()
        ttk.Entry(grid, textvariable=self.v_org).grid(row=5, column=1, sticky="we", padx=6, pady=4)

        # Make columns stretch
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        # Models (no default selection)
        lf = ttk.LabelFrame(self.tab_config, text="Models (run in parallel). Select 1+ to enable.")
        lf.pack(side=TOP, fill=X, padx=10, pady=(0, 6))
        inner = ttk.Frame(lf); inner.pack(fill=X, padx=6, pady=6)
        self.model_vars = {}
        cols = 3
        for i, (mid, label, checked, notes) in enumerate(MODEL_REGISTRY):
            var = tk.BooleanVar(value=False)  # nothing pre-selected
            cb_text = f"{label}{' — ' + notes if notes else ''}"
            cb = ttk.Checkbutton(inner, text=cb_text, variable=var)
            r, c = divmod(i, cols)
            cb.grid(row=r, column=c, sticky="w", padx=6, pady=3)
            self.model_vars[mid] = var
        for c in range(cols):
            inner.grid_columnconfigure(c, weight=1)
        self.parallel_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(lf, text="Run selected models in parallel", variable=self.parallel_var).pack(side=LEFT, padx=8)

        # Save/Load
        btns = ttk.Frame(self.tab_config); btns.pack(side=TOP, fill=X, padx=10, pady=10)
        ttk.Button(btns, text="Load Config…", command=self.load_config_via_dialog).pack(side=LEFT)
        ttk.Button(btns, text="Save Settings", command=self.save_settings).pack(side=RIGHT)

    def _apply_cfg_to_ui(self):
        self.v_corr.set(self.cfg.get("correlation_id", "default-profile"))
        self.v_gh.set(self.cfg.get("github_token", ""))
        self.v_cid.set(self.cfg.get("client_id", ""))
        self.v_csec.set(self.cfg.get("client_secret", ""))
        self.v_org.set(self.cfg.get("org", ""))
        self.v_host.set(self.cfg.get("host", ""))
        self.owner_var.set(self.cfg.get("org", ""))
        self.host_var.set(self.cfg.get("host", ""))

        selected = set(self.cfg.get("selected_models") or [])
        for mid, var in self.model_vars.items():
            var.set(mid in selected)
        self.parallel_var.set(bool(self.cfg.get("parallel_models", True)))
        try:
            host = self.host_var.get().strip()
            owner = self.owner_var.get().strip()
            if owner:
                cache_file = os.path.join(STORE_DIR, 'repo_cache', f'{owner}_repos.json')
                if os.path.exists(cache_file):
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cached = json.load(f)
                    cached_names = sorted([r.get('name', '') for r in cached if r.get('name')], key=str.lower)
                    self.repo_combo.set_completion_list([])
                    self.repo_combo.set_completion_list(cached_names)
        except Exception as e:
            print(f'Error loading cached repos: {e}')

    def _collect_selected_models(self):
        return [mid for mid, var in self.model_vars.items() if var.get()]

    def save_settings(self):
        new_corr = (self.v_corr.get() or "").strip() or "default-profile"
        new_config_path = config_path_for_correlation(new_corr)
        self.cfg.update({
            "correlation_id": new_corr,
            "github_token": self.v_gh.get().strip(),
            "client_id": self.v_cid.get().strip(),
            "client_secret": self.v_csec.get().strip(),
            # Locked values
            "gateway_base": "https://aia.gateway.dell.com/genai/dev/v1",
            "token_mode": "aia_auth",   # change to 'preissued' if you pass AIA token directly
            "scope": "",
            "custom_ca_bundle": "",
            "enable_pki_zip_patch": False,
            "pki_zip_url": "",
            "pki_pems": [
                "Dell Technologies Root Certificate Authority 2018.pem",
                "Dell Technologies Issuing CA 101_new.pem",
            ],
            "output_format": "html",
            "model": "llama-3-3-70b-instruct",
            "selected_models": self._collect_selected_models(),
            "parallel_models": bool(self.parallel_var.get()),
            "host":self.v_host.get().strip(),
            "org":self.v_org.get().strip()
        })
        save_config(new_config_path, self.cfg)
        self.config_path = new_config_path
        save_last_config_path(self.config_path)
        messagebox.showinfo("Saved", f"Settings saved to:\n{self.config_path}")

    def load_config_via_dialog(self):
        path = filedialog.askopenfilename(
            title="Load Configuration",
            initialdir=os.getcwd(),
            filetypes=[("YAML", "*.yaml"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            cfg = load_config(path)
            self.cfg = cfg
            self.config_path = path
            save_last_config_path(self.config_path)
            self._apply_cfg_to_ui()
            messagebox.showinfo("Loaded", f"Loaded configuration from: {path}")
        except Exception as e:
            messagebox.showerror("Load Config", str(e))

    # ---------------------- Pull Requests Tab ----------------------
    def _build_tab_pr(self):
        bar = ttk.LabelFrame(self.tab_pr, text="Pull Request")
        bar.pack(side=TOP, fill=X, padx=10, pady=10)

        row1 = ttk.Frame(bar); row1.pack(fill=X, padx=6, pady=6)
        ttk.Label(row1, text="PR URL:").pack(side=LEFT)
        self.pr_var = StringVar()
        ttk.Entry(row1, textvariable=self.pr_var, width=72).pack(side=LEFT, padx=6, fill=X, expand=True)
        ttk.Button(row1, text="Fetch & Review (Ensemble)", command=self.on_review).pack(side=LEFT, padx=6)

        head = ttk.LabelFrame(self.tab_pr, text="Repository (PR list)")
        head.pack(side=TOP, fill=X, padx=10, pady=(0, 6))
        row = ttk.Frame(head); row.pack(fill=X, padx=6, pady=6)

        # Host (from Configuration tab)
        ttk.Label(row, text="Host:").pack(side=LEFT)
        self.host_var = StringVar()
        self.host_var.set(self.cfg.get("host", "github.com"))
        ttk.Entry(row, textvariable=self.host_var, width=24, state="readonly").pack(side=LEFT, padx=6)

        # Owner (from Configuration tab)
        ttk.Label(row, text="Owner:").pack(side=LEFT)
        self.owner_var = StringVar()
        self.owner_var.set(self.cfg.get("org", ""))
        ttk.Entry(row, textvariable=self.owner_var, width=24, state="readonly").pack(side=LEFT, padx=6)

        # Repo autocomplete
        ttk.Label(row, text="Repo:").pack(side=LEFT)
        self.repo_var = StringVar(value="")
        self.repo_combo = AutoCompleteCombobox(row, textvariable=self.repo_var, width=30, state="normal")
        self.repo_combo.pack(side=LEFT, padx=6)

        # Buttons
        ttk.Button(row, text="Fetch Repos", command=self.on_fetch_repos).pack(side=LEFT, padx=6)
        ttk.Button(row, text="Load PRs (All states)", command=self.on_load_prs).pack(side=LEFT, padx=10)

        # Filters
        filt = ttk.LabelFrame(self.tab_pr, text="Filters")
        filt.pack(side=TOP, fill=X, padx=10, pady=(0, 6))
        fr = ttk.Frame(filt); fr.pack(fill=X, padx=6, pady=6)
        ttk.Label(fr, text="Author:").pack(side=LEFT)
        self.filter_author = StringVar()
        ttk.Entry(fr, textvariable=self.filter_author, width=18).pack(side=LEFT, padx=6)
        ttk.Label(fr, text="Title contains:").pack(side=LEFT)
        self.filter_title = StringVar()
        ttk.Entry(fr, textvariable=self.filter_title, width=32).pack(side=LEFT, padx=6)
        ttk.Label(fr, text="Status:").pack(side=LEFT)
        self.filter_status = StringVar(value="All")
        ttk.Combobox(fr, values=["All", "Open", "Merged", "Closed"], textvariable=self.filter_status,
                     state="readonly", width=10).pack(side=LEFT, padx=6)
        ttk.Button(fr, text="Apply Filters", command=self.apply_closed_pr_filters).pack(side=LEFT, padx=10)
        ttk.Button(fr, text="Clear Filters", command=self.clear_closed_pr_filters).pack(side=LEFT)

        # PRs table
        table_box = ttk.LabelFrame(self.tab_pr, text="Pull Requests")
        table_box.pack(side=TOP, fill=BOTH, expand=True, padx=10, pady=6)
        cols = ("number", "title", "author", "status", "updated", "link")
        self.closed_tree = ttk.Treeview(table_box, columns=cols, show="headings", height=18)
        self.closed_tree.heading("number", text="#")
        self.closed_tree.heading("title", text="Title")
        self.closed_tree.heading("author", text="Author")
        self.closed_tree.heading("status", text="Status")
        self.closed_tree.heading("updated", text="Updated")
        self.closed_tree.heading("link", text="Link")
        self.closed_tree.column("number", width=70, anchor="w")
        self.closed_tree.column("title", width=520, anchor="w")
        self.closed_tree.column("author", width=150, anchor="w")
        self.closed_tree.column("status", width=90, anchor="center")
        self.closed_tree.column("updated", width=150, anchor="w")
        self.closed_tree.column("link", width=200, anchor="w")
        self.closed_tree.pack(fill=BOTH, expand=True, padx=6, pady=6)

        # Bind events
        self.closed_tree.bind("<Double-1>", self.on_tree_double_click)
        self.closed_tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        try:
            host = self.host_var.get().strip()
            owner = self.owner_var.get().strip()
            if owner:
                cache_file = os.path.join(STORE_DIR, 'repo_cache', f'{owner}_repos.json')
                if os.path.exists(cache_file):
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cached = json.load(f)
                    cached_names = sorted([r.get('name', '') for r in cached if r.get('name')], key=str.lower)
                    self.repo_combo.set_completion_list([])
                    self.repo_combo.set_completion_list(cached_names)
        except Exception as e:
            print(f'Error loading cached repos: {e}')

    def on_tree_double_click(self, event):
        sel = self.closed_tree.selection()
        if not sel:
            return
        item = self.closed_tree.item(sel[0])
        vals = item.get("values", [])
        if len(vals) >= 6:
            url = vals[5]
            if url:
                webbrowser.open(url)

    def on_tree_select(self, event):
        sel = self.closed_tree.selection()
        if not sel:
            return
        item = self.closed_tree.item(sel[0])
        vals = item.get("values", [])
        if len(vals) >= 6:
            url = vals[5]
            if url:
                self.pr_var.set(url)

    def _maybe_refresh_repo_list(self):
        # Placeholder if you want to auto-refresh repos on owner/host change
        pass

    def on_fetch_repos(self):
        try:
            host = (self.host_var.get() or "github.com").strip()
            owner = (self.owner_var.get() or "").strip()
            if not owner:
                messagebox.showerror("Repos", "Select an Owner first.")
                return

            self._busy_start("Working… Fetching repositories")
            repos = fetch_all_repos_for_owner(self.cfg, host, owner)  # uses the function above
            self.repo_combo.set_completion_list([])
            self.repo_combo.set_completion_list(repos)  # your AutoCompleteCombobox
            self._busy_stop(f"Loaded {len(repos)} repos for {owner}")
        except Exception as e:
            self._busy_stop("Error")
            messagebox.showerror("Repos", str(e))

    def _pr_status(self, pr: dict) -> str:
        if pr.get("state") == "open":
            return "Open"
        return "Merged" if pr.get("merged_at") else "Closed"

    def on_load_prs(self):
        try:
            self._busy_start("Working… Loading PRs")
            host = (self.host_var.get() or "github.com").strip()
            owner = (self.owner_var.get() or self.last_owner).strip()
            repo = (self.repo_var.get() or self.last_repo).strip()
            if not owner or not repo:
                self._busy_stop("Error")
                messagebox.showerror("Pull Requests", "Please provide Owner and Repo (or select from dropdown).")
                return
            items = fetch_all_prs(self.cfg, host, owner, repo)
            self.closed_pr_items = items
            self.render_closed_prs(items)
            self.last_host, self.last_owner, self.last_repo = host, owner, repo
            self._busy_stop(f"Loaded {len(items)} PR(s) for {owner}/{repo}")
        except Exception as e:
            self._busy_stop("Error")
            messagebox.showerror("Pull Requests", str(e))

    def apply_closed_pr_filters(self):
        author = (self.filter_author.get() or "").strip().lower()
        title = (self.filter_title.get() or "").strip().lower()
        want_status = (self.filter_status.get() or "All")

        def keep(pr):
            a = ((pr.get("user") or {}).get("login", "") or "").lower()
            t = (pr.get("title", "") or "").lower()
            s = self._pr_status(pr)
            if author and author not in a:
                return False
            if title and title not in t:
                return False
            if want_status != "All" and s != want_status:
                return False
            return True

        filtered = [pr for pr in self.closed_pr_items if keep(pr)]
        self.render_closed_prs(filtered)

    def clear_closed_pr_filters(self):
        self.filter_author.set("")
        self.filter_title.set("")
        self.filter_status.set("All")
        self.render_closed_prs(self.closed_pr_items)

    def render_closed_prs(self, items):
        for iid in self.closed_tree.get_children():
            self.closed_tree.delete(iid)
        for pr in items:
            num = pr.get("number")
            title = pr.get("title", "")
            user = (pr.get("user") or {}).get("login", "")
            status = self._pr_status(pr)
            updated = pr.get("updated_at", "")
            html_url = pr.get("html_url", "")
            self.closed_tree.insert("", "end", values=(f"#{num}", title, user, status, updated, html_url))

    # ---------------------- Code Review History Tab ----------------------
    def _build_tab_history(self):
        outer = ttk.LabelFrame(self.tab_history, text="Code Review History")
        outer.pack(side=TOP, fill=BOTH, expand=True, padx=10, pady=6)
        container = ttk.Frame(outer); container.pack(fill=BOTH, expand=True)
        self.canvas_hist = tk.Canvas(container, borderwidth=0, highlightthickness=0)
        self.table_frame = ttk.Frame(self.canvas_hist)
        self.scrollbar_hist = ttk.Scrollbar(container, orient="vertical", command=self.canvas_hist.yview)
        self.canvas_hist.configure(yscrollcommand=self.scrollbar_hist.set)
        self.scrollbar_hist.pack(side=RIGHT, fill="y")
        self.canvas_hist.pack(side=LEFT, fill=BOTH, expand=True)
        self.canvas_hist.create_window((0, 0), window=self.table_frame, anchor="nw")
        self.table_frame.bind("<Configure>", lambda e: self.canvas_hist.configure(scrollregion=self.canvas_hist.bbox("all")))
        headers = ["Repo Name", "PR Number", "Review Comments", "Author", "Delete"]
        for c, h in enumerate(headers):
            lbl = ttk.Label(self.table_frame, text=h, style="Header.TLabel")
            lbl.grid(row=0, column=c, sticky=NSEW, padx=4, pady=6)
            self.table_frame.grid_columnconfigure(c, weight=1)
        style = ttk.Style()
        style.configure("Header.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("PRLink.TLabel", foreground="#0b61d8")
        style.configure("ReviewLink.TLabel", foreground="#1a7f37")
        style.configure("Cell.TLabel", foreground="#111")

    def clear_table_rows(self):
        for child in self.table_frame.winfo_children():
            info = child.grid_info()
            if info and int(info.get("row", 0)) > 0:
                child.destroy()

    def render_history(self):
        self.clear_table_rows()
        idx = load_index()
        items = sorted(idx.get("items", []), key=lambda x: x.get("timestamp", ""), reverse=True)
        if not items:
            lbl = ttk.Label(self.table_frame, text="No reviews yet. Use the 'Pull Requests' tab to submit a PR.",
                            style="Cell.TLabel")
            lbl.grid(row=1, column=0, columnspan=5, sticky="w", padx=6, pady=8)
            return
        for r, it in enumerate(items, start=1):
            pr_url = it.get("pr_url", "")
            html_path = it.get("html_path", "")
            author = (it.get("author", "") or "-").strip() or "-"
            entry_id = it.get("id", "")
            owner = it.get("owner", "")
            repo = it.get("repo", "")
            number = it.get("number", None)

            repo_lbl = ttk.Label(self.table_frame, text=human_repo(owner, repo), style="Cell.TLabel")
            repo_lbl.grid(row=r, column=0, sticky="w", padx=6, pady=4)

            pr_text = f"#{number}" if number else "-"
            pr_lbl = ttk.Label(self.table_frame, text=pr_text, style="PRLink.TLabel", cursor="hand2")
            pr_lbl.grid(row=r, column=1, sticky="w", padx=6, pady=4)
            if pr_url and number:
                pr_lbl.bind("<Button-1>", lambda e, u=pr_url: webbrowser.open(u))

            review_lbl = ttk.Label(self.table_frame, text="Open", style="ReviewLink.TLabel", cursor="hand2")
            review_lbl.grid(row=r, column=2, sticky="w", padx=6, pady=4)
            if html_path:
                review_lbl.bind("<Button-1>", lambda e, p=html_path: webbrowser.open(f"file://{os.path.abspath(p)}"))

            auth_lbl = ttk.Label(self.table_frame, text=author, style="Cell.TLabel")
            auth_lbl.grid(row=r, column=3, sticky="w", padx=6, pady=4)
            del_btn = ttk.Button(self.table_frame, text="Delete", command=lambda eid=entry_id: self.delete_entry(eid, quick=True))
            del_btn.grid(row=r, column=4, sticky="w", padx=6, pady=4)

    # ---------------------- Status Bar ----------------------
    def _build_status(self):
        bar = ttk.Frame(self); bar.pack(side=BOTTOM, fill=X)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=160)
        self.progress.pack(side=RIGHT, padx=6, pady=4)
        self.status_var = StringVar(value="Ready")
        self.status_label = tk.Label(bar, textvariable=self.status_var, bg="#fff3bf", fg="#4b4b00",
                                     padx=10, pady=4, anchor="w")
        self.status_label.pack(side=LEFT, fill=X, expand=True)
        style = ttk.Style()
        style.configure("Header.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("PRLink.TLabel", foreground="#0b61d8")
        style.configure("ReviewLink.TLabel", foreground="#1a7f37")
        style.configure("Cell.TLabel", foreground="#111")

    def set_status(self, msg: str):
        self.status_var.set(msg or "")
        try:
            self.status_label.update_idletasks()
        except Exception:
            pass

    def _busy_start(self, msg: str = "Working…"):
        self.set_status(msg)
        try:
            self.progress.start(12)
        except Exception:
            pass

    def _busy_step(self, msg: str | None = None):
        if msg:
            self.set_status(msg)
        try:
            self.progress.step(5)
        except Exception:
            pass

    def _busy_stop(self, final_msg: str | None = None):
        try:
            self.progress.stop()
        except Exception:
            pass
        self.set_status(final_msg or "Ready")

    # ---------------------- Delete Entry ----------------------
    def delete_entry(self, entry_id: str, quick: bool = True):
        if not entry_id:
            messagebox.showerror("Delete", "Entry not found.")
            return
        idx = load_index()
        items = idx.get("items", [])
        victim = next((it for it in items if it.get("id") == entry_id), None)
        if not victim:
            messagebox.showerror("Delete", "Entry not found.")
            return
        items = [it for it in items if it.get("id") != entry_id]
        idx["items"] = items
        save_index(idx)
        html_path = victim.get("html_path")
        if html_path and os.path.exists(html_path):
            try:
                os.remove(html_path)
            except Exception as e:
                messagebox.showwarning("Delete File", f"Could not delete file:\n{e}")
        self.render_history()
        self.set_status("Entry deleted")

    # ---------------------- HTML Normalization & Report ----------------------
    def _sanitize_model_anchor(self, model_name: str) -> str:
        return "m-" + "".join(ch.lower() if ch.isalnum() else "-" for ch in model_name).strip("-")

    def _now_stamp(self) -> str:
        return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    def _safe_base_filename(self, owner: str, repo: str, number: int | str, title: str) -> str:
        raw = f"{owner}-{repo}-PR{number}-{title or ''}"
        clean = "".join((c if c.isalnum() or c in ("-", "_") else "_") for c in raw)
        return clean[:180]

    def _strip_code_fences(self, text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text.strip())
        return text.strip()

    def _markdown_to_html_light(self, text: str) -> str:
        if not text:
            return ""
        if "<html" in text.lower() or "<div" in text.lower() or "<table" in text.lower() or "<section" in text.lower():
            return text
        text = self._strip_code_fences(text)

        import html as _html
        import re as _re
        esc = _html.escape
        lines = text.splitlines()
        html_lines = []
        in_ul = in_ol = in_pre = False

        def close_lists():
            nonlocal in_ul, in_ol
            if in_ul:
                html_lines.append("</ul>"); in_ul = False
            if in_ol:
                html_lines.append("</ol>"); in_ol = False

        for raw in lines:
            line = raw.rstrip("\n")

            m = _re.match(r"^\s{0,3}(#{1,6})\s+(.*)$", line)
            if m:
                close_lists()
                level = len(m.group(1))
                content = m.group(2).strip()
                html_lines.append(f"<h{level}>{esc(content)}</h{level}>")
                continue

            if _re.match(r"^\s*[-*]\s+.+$", line):
                if not in_ul:
                    close_lists()
                    in_ul = True
                    html_lines.append("<ul>")
                html_lines.append(f"<li>{esc(line.lstrip(' -*'))}</li>")
                continue

            if _re.match(r"^\s*\d+\.\s+.+$", line):
                if not in_ol:
                    close_lists()
                    in_ol = True
                    html_lines.append("<ol>")
                html_lines.append(f"<li>{esc(_re.sub(r'^\s*\d+\.\s+', '', line))}</li>")
                continue

            if _re.match(r"^\s{4,}.*$", line):
                if not in_pre:
                    close_lists()
                    in_pre = True
                    html_lines.append("<pre><code>")
                html_lines.append(esc(line[4:]))
                continue
            else:
                if in_pre and line.strip() == "":
                    html_lines.append("")
                    continue
                elif in_pre:
                    html_lines.append("</code></pre>")
                    in_pre = False

            if line.strip():
                close_lists()
                html_lines.append(f"<p>{esc(line.strip())}</p>")
            else:
                close_lists()
                html_lines.append("")

        close_lists()
        if in_pre:
            html_lines.append("</code></pre>")

        out = "\n".join(l for l in html_lines if l is not None)
        return out.strip()

    def _force_headings_blue(self, html_fragment: str) -> str:
        if not html_fragment:
            return ""
        import re as _re

        # Added "Suggested Test Cases" and "Overall Verdict"
        targets = [
            "Change Requirement",
            "Key Points",
            "Change Summary by File",
            "Review Table",
            "Suggested Test Cases",
            "Overall Verdict",
        ]

        def repl_heading(m):
            tag = m.group(1);
            inner = m.group(2)
            for t in targets:
                if inner.strip().lower() == t.lower():
                    return f"<{tag} style=\"color:#0B63C5;\">{t}</{tag}>"
            return m.group(0)

        # Color <h1>..</h1> .. <h6>..</h6>
        html_fragment = _re.sub(
            r"<(h[1-6])>([^<]+)</\1>",
            repl_heading,
            html_fragment,
            flags=_re.IGNORECASE,
        )

        # Color <p><strong>...</strong></p> and <p><b>...</b></p> variants
        for t in targets:
            html_fragment = html_fragment.replace(
                f"<p><strong>{t}</strong></p>",
                f"<p><strong style=\"color:#0B63C5;\">{t}</strong></p>"
            ).replace(
                f"<p><b>{t}</b></p>",
                f"<p><b style=\"color:#0B63C5;\">{t}</b></p>"
            )

        return html_fragment

    def _ensure_bordered_tables(self, html_fragment: str) -> str:
        if not html_fragment:
            return ""
        import re as _re

        def add_table_style(m):
            tag = m.group(0)
            if "style=" in tag:
                return tag
            return '<table style="border-collapse:collapse;width:100%;border:1px solid #cbd5e1;">'

        html_fragment = _re.sub(r"<table(\s*)>", add_table_style, html_fragment, flags=_re.IGNORECASE)
        html_fragment = html_fragment.replace("<th", "<th style=\"border:1px solid #cbd5e1;padding:8px;\"")
        html_fragment = html_fragment.replace("<td", "<td style=\"border:1px solid #cbd5e1;padding:8px;\"")
        return html_fragment

    def _normalize_model_html(self, raw_text: str) -> str:
        if not raw_text:
            return ""
        s = raw_text.strip()
        s = self._strip_code_fences(s)
        s = self._markdown_to_html_light(s)
        s = self._force_headings_blue(s)
        s = self._ensure_bordered_tables(s)
        return s

    def _save_error_log(self, model_errors: dict) -> str | None:
        if not model_errors:
            return None
        import html as _html
        errdir = os.path.join(STORE_DIR, "errorlog")
        os.makedirs(errdir, exist_ok=True)
        ts = self._now_stamp()
        path = os.path.join(errdir, f"errors-{ts}.html")
        parts = [
            "<!doctype html><html><head><meta charset='utf-8'>",
            "<title>Model Errors</title>",
            "<style>body{font-family:Inter,Segoe UI,Arial,sans-serif;padding:16px;} h1{color:#b91c1c;} ",
            "table{border-collapse:collapse;width:100%;} td,th{border:1px solid #ddd;padding:8px;text-align:left;} ",
            "th{background:#f8f8f8;}</style></head><body>",
            "<h1>Model Errors</h1>",
            "<table><thead><tr><th>Model</th><th>Error</th></tr></thead><tbody>",
        ]
        for m, e in model_errors.items():
            parts.append(
                f"<tr><td>{_html.escape(m)}</td><td><pre style='white-space:pre-wrap;'>{_html.escape(e)}</pre></td></tr>"
            )
        parts.append("</tbody></table></body></html>")
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(parts))
        return path

    def _wrap_full_report(self, title: str, pr_url: str, owner: str, repo: str, number: int | str,
                          sections: list[tuple[str, str]], failed: dict, error_log_link: str | None) -> str:
        import html as _html
        esc = _html.escape
        css = """
        :root{--blue:#0B63C5;--red:#b91c1c;--border:#cbd5e1;--muted:#6b7280;}
        *{box-sizing:border-box;}
        html,body{max-width:100%;overflow-x:hidden;}
        body{font-family:Inter,Segoe UI,Arial,sans-serif;margin:0;padding:0;background:#fff;}

        /* Slightly narrower and centered to avoid horizontal scroll on most displays */
        .container{width:min(92vw,1320px);max-width:none;margin:0;padding:24px;}

        h1{margin:0 0 10px 0;font-size:24px;}
        h2{margin:20px 0 8px 0;}
        h3{margin:14px 0 6px 0;}
        a{color:#0B63C5;text-decoration:none;}
        a:hover{text-decoration:underline;}
        .header{border-bottom:1px solid var(--border);padding-bottom:12px;margin-bottom:16px;}
        .meta{color:var(--muted);font-size:14px;overflow-wrap:anywhere;word-break:break-word;}

        /* Index table */
        .index-table{width:100%;max-width:100%;border-collapse:collapse;margin:10px 0 20px 0;table-layout:fixed;}
        .index-table th,.index-table td{
            border:1px solid var(--border);padding:8px;text-align:left;
            overflow-wrap:anywhere;word-break:break-word;white-space:normal;
        }
        .index-table th{background:#f8f8f8;}

        .model-title{color:var(--red);margin:0;}
        .back{font-size:13px;margin:2px 0 12px 0;}
        hr.sep{border:none;border-top:2px solid var(--border);margin:22px 0;}

        /* Any tables inside model sections (e.g., Review Table, Suggested Test Cases) */
        .model-section table{border-collapse:collapse;width:100%;max-width:100%;table-layout:fixed;}
        .model-section th,.model-section td{
            border:1px solid var(--border);padding:8px;text-align:left;
            overflow-wrap:anywhere;word-break:break-word;white-space:normal;
        }

        /* Images and pre/code wrap to avoid overflow */
        img{max-width:100%;height:auto;}
        pre, code{white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere;}
        """

        # Build index rows
        rows = []
        for model_name, fragment in sections:
            ok = (fragment.strip() != "")
            status = "OK" if ok else "Failed"
            rows.append(
                f"<tr><td><a href='#{self._sanitize_model_anchor(model_name)}'>{esc(model_name)}</a></td>"
                f"<td>{status}</td></tr>"
            )

        parts = []
        parts.append("<!doctype html><html><head><meta charset='utf-8'>")
        parts.append(f"<title>{esc(title)}</title>")
        parts.append(f"<style>{css}</style></head><body>")
        parts.append("<div class='container'>")

        # Header
        parts.append("<div class='header'>")
        parts.append(f"<h1>{esc(title)}</h1>")
        if pr_url:
            parts.append(
                f"<div class='meta'>PR:&nbsp;<a href='{esc(pr_url)}' target='_blank'>{esc(pr_url)}</a></div>"
            )
        parts.append(
            f"<div class='meta'>Repo: {esc(owner)}/{esc(repo)} &nbsp;&nbsp; PR #{esc(str(number))}</div>"
        )
        if error_log_link:
            parts.append(
                f"<div class='meta'>Errors:&nbsp;<a href='{esc(error_log_link)}' target='_blank'>Open Error Log</a></div>"
            )
        parts.append("</div>")

        # Index
        parts.append("<a id='index'></a>")
        parts.append("<h2>Index</h2>")
        parts.append("<table class='index-table'><thead><tr><th>Model</th><th>Status</th></tr></thead><tbody>")
        parts.extend(rows)
        parts.append("</tbody></table>")

        # Sections
        for model_name, fragment in sections:
            anchor = self._sanitize_model_anchor(model_name)
            parts.append("<hr class='sep'>")
            parts.append(f"<div class='model-section'><a id='{anchor}'></a>")
            parts.append(f"<h2 class='model-title'>{esc(model_name)}</h2>")
            parts.append("<div class='back'><a href='#index'>Back to Index</a></div>")
            if fragment.strip():
                parts.append(fragment)
            else:
                parts.append("<p><em>No output (model failed or returned empty).</em></p>")
            parts.append("</div>")

        # Failed models list
        if failed:
            parts.append("<hr class='sep'>")
            parts.append("<h2>Failed Models</h2>")
            parts.append("<ul>")
            for m, err in failed.items():
                parts.append(f"<li><strong>{esc(m)}</strong> — <span class='meta'>{esc(err)[:400]}</span></li>")
            parts.append("</ul>")

        parts.append("</div></body></html>")
        return "".join(parts)

    # ---------------------- Review Action ----------------------
    def on_review(self):
        try:
            pr_url = (self.pr_var.get() or "").strip()
            if not pr_url:
                messagebox.showerror("Error", "Enter a GitHub PR URL.")
                return

            selected_models = self._collect_selected_models()
            if not selected_models:
                messagebox.showerror("Models", "Please select one or more models in Configuration tab.")
                return

            patch_certifi_with_pki_zip(self.cfg)

            # 1) Diff
            self._busy_start("Working… Fetching PR diff")
            res = fetch_pr_diff_filtered(self.cfg, pr_url)
            diff, skipped_files = None, []
            if isinstance(res, tuple):
                if len(res) >= 1:
                    diff = res[0]
                if len(res) >= 2 and isinstance(res[1], (list, tuple)):
                    skipped_files = list(res[1])
            else:
                diff = res
            if not (diff or "").strip():
                self._busy_stop("Error")
                raise RuntimeError(
                    "No reviewable changes after excluding generated files. "
                    "Disable 'skip_generated' in Configuration to include them."
                )
            if skipped_files:
                self.set_status(f"Excluded {len(skipped_files)} generated file(s)")

            # 2) PR meta
            self._busy_step("Working… Fetching PR metadata")
            meta = fetch_pr_meta(self.cfg, pr_url) or {}
            pr_title = (meta.get("title") or "").strip() or "Pull Request"
            author = (((meta.get("user") or {}).get("login", "") or "").strip())
            host, owner, repo, number = parse_pr_url(pr_url)
            self.last_host, self.last_owner, self.last_repo = host, owner, repo
            if not self.owner_var.get():
                self.owner_var.set(owner)
            if not self.repo_var.get():
                self.repo_var.set(repo)
            if not self.host_var.get():
                self.host_var.set(host)

            # 3) Run models
            self._busy_step("Working… Running selected models")
            results: dict[str, str] = {}
            errors: dict[str, str] = {}

            from concurrent.futures import ThreadPoolExecutor, as_completed

            def run_one(mname):
                try:
                    out = single_model_review(self.cfg, mname, diff, meta)
                    return mname, out
                except Exception as e:
                    return mname, e

            if bool(self.parallel_var.get()):
                with ThreadPoolExecutor(max_workers=min(len(selected_models), 8)) as ex:
                    futs = [ex.submit(run_one, m) for m in selected_models]
                    for f in as_completed(futs):
                        mname, res2 = f.result()
                        if isinstance(res2, Exception):
                            errors[mname] = str(res2); results[mname] = ""
                        else:
                            results[mname] = res2 or ""
                        self._busy_step()
            else:
                for m in selected_models:
                    mname, res2 = run_one(m)
                    if isinstance(res2, Exception):
                        errors[mname] = str(res2); results[mname] = ""
                    else:
                        results[mname] = res2 or ""
                    self._busy_step()

            # 4) Build report (no synthesis)
            self._busy_step("Working… Building HTML report")
            sections = []
            for m in selected_models:
                raw = results.get(m, "")
                normalized = self._normalize_model_html(raw)
                sections.append((m, normalized))

            err_link = self._save_error_log(errors) if errors else None
            title = f"PR Review — {owner}/{repo} — #{number}: {pr_title}"

            full_html = self._wrap_full_report(
                title=title,
                pr_url=pr_url,
                owner=owner, repo=repo, number=number,
                sections=sections,
                failed=errors,
                error_log_link=err_link,
            )

            # 5) Save
            base = self._safe_base_filename(owner, repo, number, pr_title)
            ts = self._now_stamp()
            filename = f"{base}-{ts}.html"
            path = os.path.join(STORE_DIR, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(full_html)

            # 6) Persist to index
            idx = load_index()
            idx["items"].append({
                "id": str(uuid.uuid4()),
                "pr_url": pr_url,
                "html_path": path,
                "title": pr_title,
                "author": author,
                "owner": owner,
                "repo": repo,
                "number": number,
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            save_index(idx)

            self.render_history()

            # 7) Notify failures
            if errors:
                lines = ["Some models failed:"]
                for m, msg in errors.items():
                    lines.append(f"- {m}: {msg}")
                messagebox.showwarning("Model Failures", "\n".join(lines))

            self._busy_stop(f"Saved review → {path}")

        except Exception as e:
            self._busy_stop("Error")
            messagebox.showerror("Error", str(e))