
# Smart Code Review — GitHub PR & File‑History Reviews via LLM 

A desktop (Tkinter) app to:
- Fetch a GitHub Pull Request (PR) diff, run **multiple LLMs in parallel**, and synthesize a concise, actionable **HTML review report**.
- Browse repos/PRs, filter by author/title/status, and keep a **local history** of generated reviews.
- Generate **curated file‑history summaries** across multiple commits for a single file—each commit rendered as an HTML table with “Change Summary” and “Likely Reasons”.

> Works with an OpenAI‑compatible gateway (e.g., **Dell AIA Gateway**) using either a **preissued bearer token** or **client‑credentials (aia_auth)** flow. Supports GitHub.com and GitHub Enterprise.


## 🚀 Quick Setup & Accounts in Dell Digital Cloud

### 🛠️ Steps Overview
1. Request access to Dell Digital Cloud via the internal onboarding process.
2. Obtain your `client_id` and `client_secret` from the AIA portal.
3. These credentials required to generate an access token for  AI authentication flow and need to set in the Tool( Configuration Tab).

Refer - https://confluence.dell.com/display/AIA/Set+Up+Accounts+in+Dell+Digital+Cloud

Follow these steps to set up your Python environment:

### Step 1: Create a virtual environment
python -m venv .venv

#### Windows:
echo Activate: .\\.venv\\Scripts\\activate
##### macOS/Linux:
source .venv/bin/activate

### Step 2: Upgrade pip
python.exe -m pip install --upgrade pip

### Step 3: Install internal authentication client from Dell Artifactory
pip install aia-auth-client==0.0.6 \
  --trusted-host artifacts.dell.com \
  --extra-index-url https://artifacts.dell.com/artifactory/api/pypi/agtsdk-1007569-pypi-prd-local/simple

### Step 4: Install project dependencies
pip install -r requirements.txt

### Step 5: Run the app
python main.py



## Key Features
- **Multimodel  reviews**: Run multiple models concurrently; 
- **HTML reports**: Clean, printable output with per‑model sections, index, and an error‑log link if models fail.
- **Smart filtering**: Exclude generated artifacts from diffs using path globs, regex, and header markers.
- **GitHub tooling**:
  - Parse PR URLs, list PRs for a repo, filter by status and author, and open PRs in the browser.
  - **File History tab**: Select any GitHub *blob* URL → load commits for that file → multi‑select commits → generate curated per‑commit tables + overall narrative.
- **Config persistence**: Per‑profile YAML config; review index and HTML files stored locally.

---
