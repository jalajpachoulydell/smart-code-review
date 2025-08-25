
# PR Reviewer (Modular UI) — GitHub PR & File‑History Reviews via LLM Ensemble

A desktop (Tkinter) app to:
- Fetch a GitHub Pull Request (PR) diff, run **multiple LLMs in parallel**, and synthesize a concise, actionable **HTML review report**.
- Browse repos/PRs, filter by author/title/status, and keep a **local history** of generated reviews.
- Generate **curated file‑history summaries** across multiple commits for a single file—each commit rendered as an HTML table with “Change Summary” and “Likely Reasons”.

> Works with an OpenAI‑compatible gateway (e.g., **Dell AIA Gateway**) using either a **preissued bearer token** or **client‑credentials (aia_auth)** flow. Supports GitHub.com and GitHub Enterprise.

---

## Key Features
- **Ensemble reviews**: Run multiple models concurrently; optional synthesis to a single best review.
- **HTML reports**: Clean, printable output with per‑model sections, index, and an error‑log link if models fail.
- **Smart filtering**: Exclude generated artifacts from diffs using path globs, regex, and header markers.
- **GitHub tooling**:
  - Parse PR URLs, list PRs for a repo, filter by status and author, and open PRs in the browser.
  - **File History tab**: Select any GitHub *blob* URL → load commits for that file → multi‑select commits → generate curated per‑commit tables + overall narrative.
- **Config persistence**: Per‑profile YAML config; review index and HTML files stored locally.
- **Enterprise TLS/PKI**: Optional patching of `certifi` bundle from a corporate PKI ZIP.

---

## Architecture
```
pr_reviewer/
  __init__.py
  config.py                # default config + load/save helpers
  storage.py               # pr-code-review/ store + index.json
  tls.py                   # optional PKI ZIP patch, CA handling
  github_api.py            # PR parsing, diffs, meta, pagination, repo listing
  diff_utils.py            # chunking, file extraction from unified diff
  prompts.py               # HTML/Markdown review prompts
  model_client.py          # OpenAI-compatible client + token handling
  review_engine.py         # single-model & synthesis flows
  html_utils.py            # HTML wrapper + filenames
  ui.py                    # main Tkinter UI (tabs, widgets, events)
  file_history_tab.py      # File History tab (commits -> curated tables)
main.py                    # entry point
.env.example               # reference for environment variables
requirements.txt
```

**Data & outputs**
- Reviews and assets: `./pr-code-review/`
- Review index: `./pr-code-review/index.json`
- File‑history saved summaries: `./pr-code-review/*.html`

---

## Requirements
- **Python** 3.10+
- OS packages:  
  - Linux: `python3-tk` (or equivalent) for Tkinter  
  - Corporate environments: CA bundle/PKI as needed
- Python deps (see `requirements.txt`): `python-dotenv`, `requests`, `PyYAML`, `httpx`, `openai`, `certifi`

---

## Quick Start
```bash
# 1) Create a venv
python -m venv .venv
# Windows
echo Activate: .\\.venv\\Scripts\\activate
# macOS/Linux
source .venv/bin/activate

# 2) Install deps
pip install -r requirements.txt

# 3) Prepare environment
cp .env.example .env   # create and fill values as needed

# 4) Run the app
python main.py
```
> On Linux, install Tkinter if needed: `sudo apt-get install python3-tk`.

---

## Configuration & Authentication

### Option A — Preissued Token (simplest)
Set in `.env`:
```ini
TOKEN_MODE=preissued
AIA_ACCESS_TOKEN=<your_bearer_token>
```

### Option B — Client Credentials (aia_auth)
Set in `.env`:
```ini
TOKEN_MODE=aia_auth
CLIENT_ID=<your_client_id>
CLIENT_SECRET=<your_client_secret>
SCOPE=
```
> Requires the internal `aia-auth` package available in your environment.

### GitHub Access
Provide a **GitHub Personal Access Token** with read access:
```ini
GITHUB_TOKEN=<your_github_pat>
```
The UI supports `github.com` and a GitHub Enterprise host (selectable in the PR tab).  
For GH‑Enterprise, the REST API base is auto‑derived as `https://<host>/api/v3`.

### TLS / Corporate PKI (optional)
If you must trust an internal PKI bundle:
```ini
ENABLE_PKI_ZIP_PATCH=false
PKI_ZIP_URL=<https://your/pki.zip>
PKI_PEMS=Dell Technologies Root Certificate Authority 2018.pem,Dell Technologies Issuing CA 101_new.pem
CUSTOM_CA_BUNDLE=<path/to/ca-bundle.pem>
```

---

## Using the App

### PR Reviews
1. **Configuration** tab: paste your GitHub PAT, choose models (1+), optional parallel run.
2. **Pull Requests** tab:
   - Paste a PR URL and click **Fetch & Review (Ensemble)**, **or**
   - Choose Host/Owner/Repo → **Load PRs (All states)** → filter → select a PR.
3. The app:
   - Fetches the unified diff; optionally **filters generated files** out.
   - Runs each selected model over the (chunked) diff with a structured prompt.
   - Normalizes model outputs (HTML), builds a multi‑model report with an index.
   - Saves the HTML under `./pr-code-review/` and updates History.

### File‑History (curated per‑commit tables)
1. Open **File History** tab.
2. Paste a GitHub *file* URL (blob), e.g. `https://github.com/<owner>/<repo>/blob/<ref>/path/to/file.py`.
3. Click **Load History**, then filter and **multi‑select commits**.
4. Click **Generate Curated Summary (Models)**:
   - For each selected commit, the app fetches the **patch** for the file and **other files modified**.
   - Prompts models to create **HTML tables**: “Change Summary” (ordered list) vs “Likely Reasons”.
   - Optionally synthesizes and saves a final HTML page in `./pr-code-review/`.

---

## Model Ensemble & Synthesis
- **Model list** comes from `pr_reviewer/model_registry.py`.
- The UI executes checked models **in parallel** (configurable).
- PR reviews: the report includes one section **per model** (with normalization and tables).
- File‑history: the app can synthesize to one final fragment using a base model.

> Extend the registry, adjust prompts in `prompts.py`, or tweak normalization rules in `html_utils.py` / `file_history_tab.py`.

---

## Generated‑Code Filtering
Enabled by default; configurable in `config.yaml` / UI:
- Path globs (e.g., `**/dist/**`, `**/generated/**`, etc.)
- File regex for generated artifacts (e.g., `.g.ts`, `.Designer.cs`, protobufs)
- Header markers (e.g., “DO NOT EDIT”, “Code generated by …”)

If the filtered diff is empty, the app prompts you to disable the skip setting to include generated files.

---

## Troubleshooting
- **401 Unauthorized (GitHub)** — Verify the PAT scopes and the repository visibility/owner.
- **403 Rate‑limit/abuse detection** — Try later; minimize repeated large diff calls.
- **SSL / PKI errors** — Provide a reachable `CUSTOM_CA_BUNDLE` or enable the PKI ZIP patch.
- **Tkinter not found** — Install OS package (`python3-tk` on Debian/Ubuntu).
- **Empty/partial reviews** — Large diffs are **chunked**; consolidation merges chunked outputs.

---

## Security & Secrets Hygiene (Important)

**Never commit secrets**:
- Keep tokens in `.env` or a secret store—not in tracked files.
- Add configs with tokens to `.gitignore` (see below).
- **Rotate** any tokens that were ever committed.
- **Purge history** if secrets slipped into commits (use `git filter-repo` or BFG).

**Recommended `.gitignore` snippet**
```gitignore
# Python
__pycache__/
*.pyc
.venv/
*.egg-info/
dist/
build/

# App secrets & local state
.env
config_*.yaml
*.pem
pr-code-review/
repo_cache/
index.json
*.html
errorlog/

# OS junk
.DS_Store
Thumbs.db
```

---

## Development
- Code lives under `pr_reviewer/`; the GUI entry point is `main.py`.
- Prompts and formatting are centralized for both HTML and Markdown output.
- Consider adding unit tests for `diff_utils`, generated‑code filtering, and prompt builders.

---

## Roadmap / Ideas
- Headless CLI mode to generate reviews without the UI.
- Export to Markdown/PDF.
- Plug‑in system for additional model providers.
- Inline PR annotations via GitHub review comments API.
- Configurable synthesis toggle for PR reports.

---

## License
**TBD** — Add a company‑appropriate license file before sharing externally.

---

## Contributing
1. Fork/branch, create feature/topic branches.
2. Keep secrets out of commits.
3. Open a PR; attach sample diffs for testing.
