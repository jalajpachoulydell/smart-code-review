# file_history_tab.py
import os
import re
import json
import certifi
import requests
import datetime
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
from html import escape as _html_escape

from openai import OpenAI
import httpx

from .storage import STORE_DIR
from .html_utils import wrap_fragment_as_full_html
from .tls import patch_certifi_with_pki_zip
from .model_registry import MODEL_REGISTRY  # kept for consistency


# ---------------- Persistence for file-history summaries ----------------

FILEHIST_INDEX = os.path.join(STORE_DIR, "filehistory_index.json")

def _ensure_store():
    os.makedirs(STORE_DIR, exist_ok=True)
    if not os.path.exists(FILEHIST_INDEX):
        with open(FILEHIST_INDEX, "w", encoding="utf-8") as f:
            json.dump({"items": []}, f, ensure_ascii=False, indent=2)

def _load_hist_index():
    _ensure_store()
    try:
        with open(FILEHIST_INDEX, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": []}

def _save_hist_index(obj):
    _ensure_store()
    with open(FILEHIST_INDEX, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ---------------- Date formatting helper ----------------

def _fmt_iso_date(iso_str: str) -> str:
    """
    Convert ISO like '2025-06-26T13:24:23Z' to '26 Jun 2025'.
    Falls back to the original string if parsing fails.
    """
    if not iso_str:
        return "-"
    try:
        dt = datetime.datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%d %b %Y")
    except Exception:
        # Fallback for strings with timezone offsets, e.g. '2025-06-26T13:24:23+00:00'
        try:
            ymd = iso_str.split("T", 1)[0]
            dt = datetime.datetime.strptime(ymd, "%Y-%m-%d")
            return dt.strftime("%d %b %Y")
        except Exception:
            return iso_str  # give up gracefully


# ---------------- GitHub helpers ----------------

FILE_BLOB_URL_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<ref>[^/]+)/(?P<path>.+)$"
)

def parse_file_blob_url(file_url: str):
    m = FILE_BLOB_URL_RE.match((file_url or "").strip())
    if not m:
        raise ValueError("Invalid file URL. Expected https://<host>/<owner>/<repo>/blob/<ref>/<path>")
    return (
        m.group("host"),
        m.group("owner"),
        m.group("repo"),
        m.group("ref"),
        m.group("path"),
    )

def _github_api_base_from_host(host: str) -> str:
    return "https://api.github.com" if host.lower() == "github.com" else f"https://{host}/api/v3"

def fetch_file_commit_history(cfg: dict, file_url: str, max_commits: int = 300):
    token = (cfg.get("github_token") or "").strip()
    if not token:
        raise RuntimeError("Missing GitHub token in settings (Configuration tab).")

    host, owner, repo, ref, path = parse_file_blob_url(file_url)
    api_base = _github_api_base_from_host(host)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "pr-reviewer-ui-filehistory",
    }

    items, page, per_page = [], 1, 100
    quoted_path = requests.utils.quote(path, safe="")
    while len(items) < max_commits:
        url = f"{api_base}/repos/{owner}/{repo}/commits?path={quoted_path}&sha={ref}&per_page={per_page}&page={page}"
        r = requests.get(url, headers=headers, verify=certifi.where(), timeout=60)
        if not r.ok:
            raise RuntimeError(f"Failed to fetch commit history for file: {r.status_code} {r.text}")
        batch = r.json() or []
        if not batch:
            break
        items.extend(batch)
        if len(batch) < per_page:
            break
        page += 1

    meta = {"host": host, "owner": owner, "repo": repo, "ref": ref, "path": path}
    return items[:max_commits], meta

def fetch_commit_patch_for_file(cfg: dict, host: str, owner: str, repo: str, sha: str, file_path: str):
    """
    Returns a tuple: (patch for the target file, list of other modified files)
    """
    token = (cfg.get("github_token") or "").strip()
    if not token:
        raise RuntimeError("Missing GitHub token in settings (Configuration tab).")

    api_base = _github_api_base_from_host(host)
    url = f"{api_base}/repos/{owner}/{repo}/commits/{sha}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "pr-reviewer-ui-filehistory",
    }
    r = requests.get(url, headers=headers, verify=certifi.where(), timeout=60)
    if not r.ok:
        raise RuntimeError(f"Failed to fetch commit detail: {r.status_code} {r.text}")
    data = r.json()

    patch_for_target = ""
    others = []
    for f in (data.get("files") or []):
        fname = (f.get("filename") or "")
        prev = (f.get("previous_filename") or "")
        if fname == file_path or prev == file_path:
            patch_for_target = f.get("patch") or ""
        else:
            if fname:
                others.append(fname)

    return patch_for_target, others


# ---------------- Model helpers (OpenAI-compatible Gateway) ----------------

def _get_gateway_token(cfg: dict) -> str:
    mode = (cfg.get("token_mode") or "preissued").lower()
    if mode == "preissued":
        tok = (cfg.get("aia_access_token") or "").strip()
        if not tok:
            raise RuntimeError("token_mode is 'preissued' but 'aia_access_token' is empty.")
        return tok
    elif mode == "aia_auth":
        try:
            from aia_auth import auth
        except Exception as e:
            raise RuntimeError(
                "token_mode is 'aia_auth' but 'aia_auth' is not installed. "
                "Install your internal package: aia-auth-client==0.0.6"
            ) from e
        cid = (cfg.get("client_id") or "").strip()
        csec = (cfg.get("client_secret") or "").strip()
        if not cid or not csec:
            raise RuntimeError("CLIENT_ID/CLIENT_SECRET are required in 'aia_auth' mode.")
        token_resp = auth.client_credentials(cid, csec)
        if not getattr(token_resp, "token", None):
            raise RuntimeError(f"aia_auth returned no token: {token_resp}")
        return token_resp.token
    else:
        raise RuntimeError(f"Unknown token_mode: {mode}")

def _make_client(cfg: dict) -> OpenAI:
    patch_certifi_with_pki_zip(cfg)  # corporate PKI if configured
    token = _get_gateway_token(cfg)
    http_client = httpx.Client(verify=certifi.where())
    client = OpenAI(
        base_url=(cfg.get("gateway_base") or "").rstrip("/"),
        http_client=http_client,
        api_key=token,
    )
    return client


# ---------------- Minimal HTML helpers (normalization & styling) ----------------

def _escape_html(s: str) -> str:
    return _html_escape(s or "", quote=False)

def _unwrap_code_fences(s: str) -> str:
    """
    Remove surrounding triple backtick fences if present (```...```), any language hint ignored.
    """
    if not s:
        return s
    text = s.strip()
    if text.startswith("```") and text.endswith("```"):
        # strip first line's ```lang and trailing ```
        lines = text.splitlines()
        if len(lines) >= 2:
            lines = lines[1:-1]
            return "\n".join(lines)
        return text.strip("`")
    return text

def _has_html_tags(s: str) -> bool:
    """
    Heuristic: treat as HTML if it contains common block tags.
    """
    t = s.lower()
    return any(tag in t for tag in ("<html", "<body", "<div", "<p", "<table", "<section", "<ul", "<ol", "<h1", "<h2", "<h3", "<h4", "<h5", "<h6"))

def _highlight_keywords(html: str) -> str:
    """
    Make these labels blue when they appear as plain text (best-effort):
      Change Requirement, Key Points, Change Summary by File, Review Table
    """
    blue = ' style="color:#004c99"'
    replacements = [
        ("Change Requirement", f"<span{blue}><strong>Change Requirement</strong></span>"),
        ("Key Points", f"<span{blue}><strong>Key Points</strong></span>"),
        ("Change Summary by File", f"<span{blue}><strong>Change Summary by File</strong></span>"),
        ("Review Table", f"<span{blue}><strong>Review Table</strong></span>"),
    ]
    out = html
    for needle, repl in replacements:
        out = re.sub(rf"(?<![>\w]){re.escape(needle)}(?![\w<])", repl, out)
    return out

# --- Minimal Markdown -> HTML (headings, bullets, hr, pipe tables with borders) ---

def _md_table_to_html(lines):
    """Convert a simple GitHub-style pipe table block into HTML with borders."""
    rows = [ln.strip().strip("|").strip() for ln in lines if "|" in ln]
    if not rows:
        return ""
    # header and optional separator
    header = [c.strip() for c in rows[0].split("|")]
    if len(rows) >= 2 and set(rows[1].replace("|", "").strip()) <= set("-:"):
        data_rows = rows[2:]
    else:
        data_rows = rows[1:]

    ths = "".join(f'<th style="border:1px solid #004c99;padding:8px;vertical-align:top">{_escape_html(h)}</th>' for h in header)
    trs = "".join(
        "<tr>" + "".join(f'<td style="border:1px solid #004c99;padding:8px;vertical-align:top">{_escape_html(c)}</td>' for c in r.split("|")) + "</tr>"
        for r in data_rows
    )
    return (
        '<table style="border-collapse:collapse;width:100%;margin:10px 0;border:1px solid #004c99">'
        "<thead><tr>" + ths + "</tr></thead>"
        "<tbody>" + trs + "</tbody></table>"
    )

def _markdown_to_html_basic(md: str) -> str:
    """Very small converter for headings, lists, hr, and simple pipe tables."""
    if not md:
        return ""
    lines = (md or "").splitlines()
    html_parts = []
    in_ul = False
    in_table = False
    table_buf = []

    def flush_ul():
        nonlocal in_ul
        if in_ul:
            html_parts.append("</ul>")
            in_ul = False

    def flush_table():
        nonlocal in_table, table_buf
        if in_table and table_buf:
            html_parts.append(_md_table_to_html(table_buf))
        in_table = False
        table_buf = []

    for raw in lines:
        line = raw.rstrip()

        # table detection: lines with pipes; keep contiguous block
        if "|" in line and not line.lstrip().startswith("```"):
            flush_ul()
            in_table = True
            table_buf.append(line)
            continue
        else:
            if in_table:
                flush_table()

        # horizontal rule
        if line.strip() in ("---", "***", "___"):
            flush_ul()
            html_parts.append('<hr style="border:none;border-top:1px solid #ddd;margin:10px 0">')
            continue

        # headings
        for level in range(6, 0, -1):
            prefix = "#" * level + " "
            if line.startswith(prefix):
                flush_ul()
                content = _escape_html(line[len(prefix):])
                html_parts.append(f"<h{level}>{content}</h{level}>")
                break
        else:
            # bullets
            if line.lstrip().startswith(("* ", "- ")):
                if not in_ul:
                    html_parts.append("<ul>")
                    in_ul = True
                bullet = line.lstrip()[2:]
                html_parts.append(f"<li>{_escape_html(bullet)}</li>")
                continue

            # blank line
            if not line.strip():
                flush_ul()
                html_parts.append("<br>")
                continue

            # paragraph
            flush_ul()
            html_parts.append(f"<p>{_escape_html(line)}</p>")

    flush_ul()
    flush_table()
    return "".join(html_parts)

def normalize_model_fragment(raw: str) -> str:
    """
    Accept model output that could be HTML, Markdown code-fenced, or plain text.
    Return safe HTML fragment with blue highlights for key labels, and force table borders.
    """
    if raw is None:
        return "<p>(No output)</p>"
    if not isinstance(raw, str):
        raw = str(raw)

    s = _unwrap_code_fences(raw).strip()
    if s.startswith("```"):  # any leftover fences
        s = s.strip("`")

    if _has_html_tags(s):
        html = s
    else:
        # Convert Markdown (headings/lists/tables) → HTML; else paragraphs/BRs.
        html = _markdown_to_html_basic(s)

    # Make sure common labels are blue everywhere
    html = _highlight_keywords(html)

    # Enforce visible borders on any tables that slipped through
    html = html.replace("<table", '<table style="border-collapse:collapse;width:100%;margin:10px 0;border:1px solid #004c99"') \
               .replace("<th", '<th style="border:1px solid #004c99;padding:8px;vertical-align:top"') \
               .replace("<td", '<td style="border:1px solid #004c99;padding:8px;vertical-align:top"')

    return html


# ---------------- Prompt builders (HTML-first with per-commit TABLES) ----------------

def build_file_history_prompts(cfg: dict):
    """
    HTML-first: ask model for curated code changes + likely reasons (bullets),
    rendered as a TABLE PER COMMIT (each commit its own block). Include “Other Files Modified” if present.
    """
    output_format = (cfg.get("output_format") or "html").lower().strip()
    if output_format == "html":
        system = (
            "You are a senior software engineer summarizing changes to ONE file across multiple commits. "
            "Return only valid HTML (no markdown). For EACH commit, render a bordered table block:\n"
            "  - Table header with: Commit <shortsha> — <date> — <author>\n"
            "  - Two columns:\n"
            "      * Left: <strong>Change Summary</strong> as an ordered list (precise, code-aware, concise). "
            "               If provided, add a small sub-section titled <em>Other Files Modified</em> as a bulleted list.\n"
            "      * Right: <strong>Likely Reasons</strong> as bullet points (speculative but grounded in message/patch)\n"
            "Do NOT perform a formal review or assign severities; this is descriptive and explanatory.\n"
            "Conclude with an <strong>Overall Narrative</strong> as bullet points."
        )
        template = """
<section>
  <h2 style="color:#004c99">Per-Commit Code Change Summary</h2>

  <!-- Repeat this TABLE per commit -->
  <table style="border-collapse:collapse; width:100%; margin:14px 0; border:1px solid #ddd;">
    <thead>
      <tr>
        <th colspan="2" style="text-align:left; background:#f8f8f8; padding:8px; border-bottom:1px solid #ddd;">
          Commit &lt;shortsha> — &lt;date> — <author>
        </th>
      </tr>
      <tr>
        <th style="text-align:left; padding:8px; border-bottom:1px solid #eee; width:50%;" style="color:#004c99;">Change Summary</th>
        <th style="text-align:left; padding:8px; border-bottom:1px solid #eee; width:50%;" style="color:#004c99;">Likely Reasons</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="vertical-align:top; padding:10px;">
          <ol>
            <li>&lt;Precise step 1: what changed, why it matters></li>
            <li>&lt;Precise step 2></li>
          </ol>
          <!-- Optional: only if provided -->
          <div><strong>Other Files Modified</strong>
            <ul>
              <li>&lt;other/file1></li>
              <li>&lt;other/file2></li>
            </ul>
          </div>
        </td>
        <td style="vertical-align:top; padding:10px;">
          <ul>
            <li>&lt;Reason 1 (concise, inferred from context)></li>
            <li>&lt;Reason 2></li>
          </ul>
        </td>
      </tr>
    </tbody>
  </table>

  <h2 style="color:#004c99">Overall Narrative</h2>
  <ul>
    <li>&lt;Crisp point 1 about the direction of changes></li>
    <li>&lt;Crisp point 2 about effects/impact></li>
    <li>&lt;Crisp point 3 about follow-ups/risks if any></li>
  </ul>
</section>
""".strip()
        hint = "Return a single HTML fragment only (no <html> wrapper)."
        return system, template, hint
    else:
        system = (
            "You are a senior software engineer summarizing changes to a single file across multiple commits. "
            "Return structured Markdown with a table-like layout per commit. Change Summary as numbered bullets; "
            "Likely Reasons as bullet points. If other files are provided, include an 'Other Files Modified' bulleted list. "
            "Conclude with 'Overall Narrative' bullets."
        )
        template = (
            "## Commit <shortsha> — <date> — <author>\n"
            "### Change Summary\n"
            "1. Step 1\n2. Step 2\n"
            "#### Other Files Modified\n"
            "- other/file1\n- other/file2\n\n"
            "### Likely Reasons\n"
            "- reason 1\n- reason 2\n\n"
            "## Overall Narrative\n"
            "- point 1\n- point 2\n- point 3"
        )
        hint = "One section per commit; Overall Narrative as bullets."
        return system, template, hint


# ---------------- Model exec: single-model + synthesis ----------------

def _single_model_file_history_summary(cfg: dict, model_name: str, multi_commit_diff_text: str, header_meta: dict) -> str:
    """
    Run a single model over the combined multi-commit diff text.
    header_meta keys: owner, repo, path, selected_shas (list), selected_count, filtered_count
    """
    client = _make_client(cfg)
    system, template, hint = build_file_history_prompts(cfg)

    header = []
    header.append(f"Repository: {header_meta.get('owner','')}/{header_meta.get('repo','')}")
    header.append(f"File: {header_meta.get('path','')}")
    header.append(f"Selected commits: {', '.join([s[:7] for s in header_meta.get('selected_shas', [])])}")
    header.append(f"Selected count: {int(header_meta.get('selected_count') or 0)}")
    header.append(f"Filtered list size: {int(header_meta.get('filtered_count') or 0)}")
    header.append(hint)

    user_content = "\n".join(header) + "\n\n" + "```diff\n" + multi_commit_diff_text + "\n```"

    completion = client.chat.completions.create(
        extra_headers={"x-correlation-id": (cfg.get("correlation_id") or "pr-review-ui-filehistory")},
        model=model_name,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": template}]},
            {"role": "user", "content": [{"type": "text", "text": user_content}]},
        ],
        stream=False,
        temperature=0.2,
    )
    return completion.choices[0].message.content

def synthesize_file_history_with_base(cfg: dict, base_model: str, summaries_by_model: dict, header_meta: dict) -> str:
    """
    Synthesize multiple model outputs into one final (HTML/Markdown) preserving per-commit sections.
    """
    client = _make_client(cfg)
    system, template, hint = build_file_history_prompts(cfg)

    srcs = []
    for m, content in summaries_by_model.items():
        if content:
            srcs.append(f"### Model: {m}\n{content}")

    synth_user = (
        "You are given multiple per-commit summaries for the SAME file. "
        "Produce a SINGLE best summary that follows the requested template, preserving distinct TABLE blocks per commit. "
        "Merge overlapping points, keep concrete descriptions, include 'Other Files Modified' sections only when present, "
        "and ensure 'Likely Reasons' are concise bullet points. "
        "Conclude with a single 'Overall Narrative' expressed as bullet points."
    )

    header = []
    header.append(f"Repository: {header_meta.get('owner','')}/{header_meta.get('repo','')}")
    header.append(f"File: {header_meta.get('path','')}")
    header.append(f"Selected commits: {', '.join([s[:7] for s in header_meta.get('selected_shas', [])])}")
    header.append(f"Selected count: {int(header_meta.get('selected_count') or 0)}")
    header.append(f"Filtered list size: {int(header_meta.get('filtered_count') or 0)}")
    header.append(hint)

    completion = client.chat.completions.create(
        extra_headers={"x-correlation-id": (cfg.get("correlation_id") or "pr-review-ui-filehistory")},
        model=base_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": template}]},
            {"role": "user", "content": [{"type": "text", "text": "\n".join(header)}]},
            {"role": "user", "content": [{"type": "text", "text": synth_user}]},
            {"role": "user", "content": [{"type": "text", "text": "\n\n".join(srcs) if srcs else "No sources."}]},
        ],
        stream=False,
        temperature=0.2,
    )
    return completion.choices[0].message.content


# ---------------- Diff block builder ----------------

def build_multi_commit_diff_block(commits_meta: list, file_path: str) -> str:
    """
    Assemble text with separate sections per commit in provided order (display order).
    Inserts markers for per-commit PATCH and OTHER FILES lists. Dates are formatted.
    """
    lines = []
    for meta in commits_meta:
        sha_full = (meta.get("sha") or "")[:40]
        short = sha_full[:7]
        commit = meta.get("commit") or {}
        author_line = ((commit.get("author") or {}).get("name") or (meta.get("author") or {}).get("login") or "-")
        raw_date = (commit.get("author") or {}).get("date") or "-"
        date_line = _fmt_iso_date(raw_date)
        msg = (commit.get("message") or "").splitlines()[0][:220]

        lines.append(f"=== Commit {short} — {date_line} — {author_line} ===")
        if msg:
            lines.append(f"Message: {msg}")
        lines.append(f"File: {file_path}")

        # markers to be replaced later
        lines.append(f"[[PATCH::{sha_full}]]")
        lines.append(f"[[OTHERS::{sha_full}]]")

        lines.append("")  # spacer
    return "\n".join(lines)


# ---------------- UI Tab ----------------

class FileHistoryTab:
    """
    A Notebook tab that:
      - Loads commit history for a given file URL
      - Filters by Author and Title/Message
      - Lets you MULTI-SELECT commits from the CURRENT FILTERED LIST
      - Calls selected models to produce curated code change summaries + bullet reasons
      - Renders each commit as its own HTML table block
      - Synthesizes, saves to HTML, and lists saved summaries with Open/Delete (buttons on TOP-LEFT)
    Depends on main App for:
      - app.cfg (dict), app._collect_selected_models(), app.parallel_var (boolvar),
        app.set_status(str), app._busy_start/_busy_step/_busy_stop
    """
    def __init__(self, app, notebook: ttk.Notebook):
        self.app = app
        self.cfg = app.cfg
        self.nb = notebook

        # One tab
        self.frame = ttk.Frame(self.nb)
        self.nb.add(self.frame, text="File History")

        # --- Top: file URL + load ---
        top = ttk.LabelFrame(self.frame, text="GitHub File URL")
        top.pack(side=tk.TOP, fill=tk.X, padx=10, pady=8)

        row = ttk.Frame(top); row.pack(fill=tk.X, padx=6, pady=6)
        ttk.Label(row, text="File URL:").pack(side=tk.LEFT)
        self.file_url_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.file_url_var, width=90).pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)
        ttk.Button(row, text="Load History", command=self.on_load_file_history).pack(side=tk.LEFT, padx=6)

        # --- Filters ---
        filt = ttk.LabelFrame(self.frame, text="Filters")
        filt.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 6))

        fr = ttk.Frame(filt); fr.pack(fill=tk.X, padx=6, pady=6)
        ttk.Label(fr, text="Author contains:").pack(side=tk.LEFT)
        self.filter_author = tk.StringVar()
        ttk.Entry(fr, textvariable=self.filter_author, width=18).pack(side=tk.LEFT, padx=6)

        ttk.Label(fr, text="Title/Message contains:").pack(side=tk.LEFT)
        self.filter_title = tk.StringVar()
        ttk.Entry(fr, textvariable=self.filter_title, width=32).pack(side=tk.LEFT, padx=6)

        ttk.Button(fr, text="Apply Filters", command=self.apply_filters).pack(side=tk.LEFT, padx=10)
        ttk.Button(fr, text="Clear Filters", command=self.clear_filters).pack(side=tk.LEFT)

        # --- Commit History (with the Generate button on TOP-LEFT) ---
        table_frame = ttk.LabelFrame(self.frame, text="Commit History (newest first; filtered)")
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=6)

        # Button row (top-left of this section)
        btnrow = ttk.Frame(table_frame)
        btnrow.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(6, 0))
        ttk.Button(btnrow, text="Generate Curated Summary (Models)", command=self.on_generate_summary).pack(side=tk.LEFT, padx=4)

        cols = ("sha", "date", "author", "message", "link")
        self.file_commits_tree = ttk.Treeview(
            table_frame, columns=cols, show="headings", height=15, selectmode="extended"
        )
        for c, title, width in [
            ("sha", "SHA", 120),
            ("date", "Date", 170),
            ("author", "Author", 160),
            ("message", "Message", 540),
            ("link", "Link", 240),
        ]:
            self.file_commits_tree.heading(c, text=title)
            self.file_commits_tree.column(c, width=width, anchor="w")

        self.file_commits_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.file_commits_tree.yview)
        sb.pack(side=tk.RIGHT, fill="y")
        self.file_commits_tree.configure(yscrollcommand=sb.set)

        self._row_to_commit = {}
        self.file_commits_tree.bind("<Double-1>", self._on_tree_double_click)

        # --- Saved summaries (Open/Delete buttons TOP-LEFT) ---
        saved_box = ttk.LabelFrame(self.frame, text="Saved Summaries")
        saved_box.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=6)

        # Buttons on TOP-LEFT of this section
        saved_btnrow = ttk.Frame(saved_box)
        saved_btnrow.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(6, 0))
        ttk.Button(saved_btnrow, text="Open Selected", command=self.on_open_saved).pack(side=tk.LEFT, padx=4)
        ttk.Button(saved_btnrow, text="Delete Selected", command=self.on_delete_saved).pack(side=tk.LEFT, padx=4)

        # List (below the buttons)
        self.saved_tree = ttk.Treeview(saved_box, columns=("time","repo_file","range","path"), show="headings", height=8)
        self.saved_tree.heading("time", text="Saved At")
        self.saved_tree.heading("repo_file", text="Repo/File")
        self.saved_tree.heading("range", text="Commit Range")
        self.saved_tree.heading("path", text="File Path")
        self.saved_tree.column("time", width=170, anchor="w")
        self.saved_tree.column("repo_file", width=430, anchor="w")
        self.saved_tree.column("range", width=220, anchor="w")
        self.saved_tree.column("path", width=440, anchor="w")
        self.saved_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)

        sb2 = ttk.Scrollbar(saved_box, orient="vertical", command=self.saved_tree.yview)
        sb2.pack(side=tk.RIGHT, fill="y")
        self.saved_tree.configure(yscrollcommand=sb2.set)

        # State
        self._last_file_meta = None
        self._all_commits = []
        self._filtered_commits_cache = []

        # Initial load of saved summaries
        self.refresh_saved_list()

    # ----- Events -----

    def _on_tree_double_click(self, event):
        sel = self.file_commits_tree.selection()
        if not sel:
            return
        item = self.file_commits_tree.item(sel[0])
        vals = item.get("values", [])
        if len(vals) >= 5 and vals[4]:
            webbrowser.open(vals[4])

    # ----- Actions -----

    def on_load_file_history(self):
        try:
            url = (self.file_url_var.get() or "").strip()
            if not url:
                messagebox.showerror("File History", "Enter a GitHub file URL.")
                return

            self.app._busy_start("Working… Loading file commit history")
            commits, meta = fetch_file_commit_history(self.app.cfg, url, max_commits=300)
            self._last_file_meta = meta
            self._all_commits = commits

            self.apply_filters()
            self.app.set_status(f"Loaded {len(commits)} commits for {meta.get('path','')}")
            self.app._busy_stop("Loaded file history")
        except Exception as e:
            self.app._busy_stop("Error")
            messagebox.showerror("File History", str(e))
            self.app.set_status("Error")

    def _render_commit_table(self, commits, meta):
        self._row_to_commit.clear()
        for iid in self.file_commits_tree.get_children():
            self.file_commits_tree.delete(iid)
        host, owner, repo = meta["host"], meta["owner"], meta["repo"]
        for c in commits:
            sha = (c.get("sha") or "")[:40]
            short = sha[:7]
            commit = c.get("commit") or {}
            author = ((commit.get("author") or {}).get("name") or (c.get("author") or {}).get("login") or "-")
            date_iso = (commit.get("author") or {}).get("date") or "-"
            date = _fmt_iso_date(date_iso)
            msg = (commit.get("message") or "").splitlines()[0]
            link = f"https://{host}/{owner}/{repo}/commit/{sha}"
            iid = self.file_commits_tree.insert("", "end", values=(short, date, author, msg, link))
            self._row_to_commit[iid] = c

    def _apply_filter_logic(self, commits):
        a = (self.filter_author.get() or "").strip().lower()
        t = (self.filter_title.get() or "").strip().lower()

        def keep(c):
            commit = c.get("commit") or {}
            author = ((commit.get("author") or {}).get("name") or (c.get("author") or {}).get("login") or "")
            msg = (commit.get("message") or "")
            if a and a not in (author or "").lower():
                return False
            if t and t not in (msg or "").lower():
                return False
            return True

        return [c for c in commits if keep(c)]

    def apply_filters(self):
        if not self._last_file_meta:
            return
        filtered = self._apply_filter_logic(self._all_commits)
        self._filtered_commits_cache = filtered
        self._render_commit_table(filtered, self._last_file_meta)

    def clear_filters(self):
        self.filter_author.set("")
        self.filter_title.set("")
        self.apply_filters()

    def on_generate_summary(self):
        """
        Build a curated HTML/Markdown change summary via selected models for:
          ONLY the commits MULTI-SELECTED from the CURRENT FILTERED LIST.
          Each commit is rendered as a dedicated HTML table block.
          Include 'Other Files Modified' per commit if non-empty.
        """
        try:
            if not self._last_file_meta or not self._filtered_commits_cache:
                messagebox.showerror("File History", "Load history and/or apply filters first.")
                return

            sel_iids = self.file_commits_tree.selection()
            if not sel_iids:
                messagebox.showerror("File History", "Select one or more commits from the table (Ctrl/Shift click).")
                return

            # Sort selected rows by their on-screen order
            sel_iids_sorted = sorted(sel_iids, key=lambda iid: self.file_commits_tree.index(iid))
            chosen = [self._row_to_commit[iid] for iid in sel_iids_sorted]

            host = self._last_file_meta["host"]
            owner = self._last_file_meta["owner"]
            repo = self._last_file_meta["repo"]
            fpath = self._last_file_meta["path"]

            # Build scaffold text with metadata markers for each commit
            multi_template = build_multi_commit_diff_block(chosen, fpath)

            # Fetch patch & other files for each chosen commit and substitute into scaffold
            self.app._busy_start("Working… Fetching per-commit patches")
            combined = multi_template
            selected_shas = []
            for c in chosen:
                sha_full = (c.get("sha") or "")[:40]
                selected_shas.append(sha_full)
                patch, others = fetch_commit_patch_for_file(self.app.cfg, host, owner, repo, sha_full, fpath)

                # Replace patch marker
                combined = combined.replace(
                    f"[[PATCH::{sha_full}]]", patch or "(No patch for this file in this commit)"
                )

                # Replace others marker (skip if empty)
                if others:
                    others_block = "Other files modified:\n" + "\n".join(f"- {o}" for o in others)
                    combined = combined.replace(f"[[OTHERS::{sha_full}]]", others_block)
                else:
                    combined = combined.replace(f"[[OTHERS::{sha_full}]]", "")

                self.app._busy_step()

            # Run selected models
            selected_models = self.app._collect_selected_models()
            if not selected_models:
                messagebox.showerror("File History", "Select at least one model in Configuration.")
                self.app._busy_stop("Ready")
                return

            results = {}
            errors = {}

            def run_one(mname):
                try:
                    header_meta = {
                        "owner": owner, "repo": repo, "path": fpath,
                        "selected_shas": selected_shas,
                        "selected_count": len(selected_shas),
                        "filtered_count": len(self._filtered_commits_cache),
                    }
                    out = _single_model_file_history_summary(self.app.cfg, mname, combined, header_meta)
                    return mname, out
                except Exception as e:
                    return mname, e

            self.app._busy_start("Working… Generating summaries with selected models")
            if bool(self.app.parallel_var.get()):
                import concurrent.futures as futures
                with futures.ThreadPoolExecutor(max_workers=min(len(selected_models), 8)) as ex:
                    futs = [ex.submit(run_one, m) for m in selected_models]
                    for f in futures.as_completed(futs):
                        mname, res = f.result()
                        if isinstance(res, Exception):
                            errors[mname] = str(res); results[mname] = ""
                        else:
                            results[mname] = res
                        self.app._busy_step()
            else:
                for m in selected_models:
                    mname, res = run_one(m)
                    if isinstance(res, Exception):
                        errors[mname] = str(res); results[mname] = ""
                    else:
                        results[mname] = res
                    self.app._busy_step()

            # Synthesize final with base model
            self.app._busy_start("Working… Synthesizing final curated summary")
            final_fragment = synthesize_file_history_with_base(
                self.app.cfg, "llama-3-3-70b-instruct", results,
                {
                    "owner": owner, "repo": repo, "path": fpath,
                    "selected_shas": selected_shas,
                    "selected_count": len(selected_shas),
                    "filtered_count": len(self._filtered_commits_cache),
                }
            )

            # Normalize (convert Markdown from model -> HTML, enforce borders & blue labels)
            normalized_final = normalize_model_fragment(final_fragment)

            # If there were failed models, append a section at the end
            failed_section = ""
            if errors:
                lis = "".join(
                    f"<li><strong>{_escape_html(m)}</strong>: {_escape_html(msg)}</li>"
                    for m, msg in errors.items()
                )
                failed_section = (
                    '<hr style="border:none;border-top:2px solid #ddd;margin:16px 0">'
                    '<h3 style="color:#a40000;margin:8px 0;">Failed Models</h3>'
                    f"<ul>{lis}</ul>"
                )

            combined_fragment = normalized_final + failed_section

            # Save full HTML (even if cfg says markdown, we embed as <pre>); wrapper ensures consistent page.
            is_html = ((self.app.cfg.get("output_format") or "html").lower() == "html")
            full_html = wrap_fragment_as_full_html(combined_fragment, is_html_fragment=is_html)

            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            base = os.path.basename(fpath).replace(os.sep, "_")
            rng = (
                f"{(chosen[0].get('sha') or '')[:7]}..{(chosen[-1].get('sha') or '')[:7]}"
                if len(chosen) > 1 else (chosen[0].get('sha') or "")[:7]
            )
            fname = f"{owner}-{repo}-FILEHIST-CURATED-{base}-{rng}-{ts}.html"
            path = os.path.join(STORE_DIR, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(full_html)

            # Persist in Saved Summaries
            repo_file = f"{owner}/{repo} — {fpath}"
            idx_obj = _load_hist_index()
            idx_obj["items"].append({
                "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "repo_file": repo_file,
                "commit_range": rng,
                "path": path,
            })
            _save_hist_index(idx_obj)
            self.refresh_saved_list()

            # Also pop a warning with failures (optional)
            if errors:
                lines = ["Some models failed:"] + [f"- {m}: {msg}" for m, msg in errors.items()]
                messagebox.showwarning("Model Failures", "\n".join(lines))

            self.app._busy_stop(f"Saved: {path}")
            self.app.set_status(f"Saved: {path}")

        except Exception as e:
            self.app._busy_stop("Error")
            messagebox.showerror("File History", str(e))
            self.app.set_status("Error")

    # ----- Saved summaries list -----

    def refresh_saved_list(self):
        idx = _load_hist_index()
        items = sorted(idx.get("items", []), key=lambda x: x.get("saved_at",""), reverse=True)
        for iid in self.saved_tree.get_children():
            self.saved_tree.delete(iid)
        for it in items:
            saved_at = it.get("saved_at","")
            repo_file = it.get("repo_file","")
            rng = it.get("commit_range","")
            path = it.get("path","")
            self.saved_tree.insert("", "end", values=(saved_at, repo_file, rng, path))

    def on_open_saved(self):
        sel = self.saved_tree.selection()
        if not sel:
            messagebox.showinfo("Saved Summaries", "Select a row first.")
            return
        vals = self.saved_tree.item(sel[0]).get("values", [])
        if len(vals) >= 4 and vals[3]:
            webbrowser.open(f"file://{os.path.abspath(vals[3])}")

    def on_delete_saved(self):
        sel = self.saved_tree.selection()
        if not sel:
            messagebox.showinfo("Saved Summaries", "Select a row to delete.")
            return
        vals = self.saved_tree.item(sel[0]).get("values", [])
        if len(vals) < 4:
            return
        path = vals[3]

        idx = _load_hist_index()
        items = idx.get("items", [])
        items = [it for it in items if it.get("path") != path]
        idx["items"] = items
        _save_hist_index(idx)

        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                messagebox.showwarning("Delete File", f"Could not delete file:\n{e}")

        self.refresh_saved_list()
        self.app.set_status("Summary deleted")