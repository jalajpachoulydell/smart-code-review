
import datetime
import re

def wrap_fragment_as_full_html(fragment: str, is_html_fragment: bool) -> str:
    if is_html_fragment:
        body_inner = fragment
    else:
        body_inner = "<pre style='white-space:pre-wrap'>" + (
            fragment.replace("&","&").replace("<","<").replace(">",">")
        ) + "</pre>"
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>PR Review</title>
<style>
body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; padding: 24px; line-height: 1.45; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; }}
th {{ background: #f6f6f6; text-align: left; }}
code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
h2 {{ border-bottom: 1px solid #eee; padding-bottom: 4px; }}
a {{ color: #0b61d8; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
{body_inner}
</body>
</html>"""

def safe_filename(owner: str, repo: str, number: int, title: str = "") -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug_title = re.sub(r"[^a-zA-Z0-9._-]+", "-", (title or "")).strip("-")[:60]
    base = f"{owner}-{repo}-PR{number}-{ts}"
    if slug_title:
        base += f"-{slug_title}"
    return base + ".html"

def human_repo(owner: str, repo: str) -> str:
    return f"{owner}/{repo}" if owner and repo else "-"
