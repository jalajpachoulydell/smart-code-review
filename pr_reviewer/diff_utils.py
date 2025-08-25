
import re

def chunk_text(s: str, max_chars: int = 12000):
    parts, cur, size = [], [], 0
    for ln in s.splitlines(keepends=True):
        ln_len = len(ln)
        if size + ln_len > max_chars and cur:
            parts.append("".join(cur))
            cur, size = [ln], ln_len
        else:
            cur.append(ln); size += ln_len
    if cur:
        parts.append("".join(cur))
    return parts

FILE_HEADER_RE = re.compile(r"^diff --git a/(?P<a>.+) b/(?P<b>.+)$", re.MULTILINE)

def extract_changed_files(diff_text: str):
    files, seen, uniq = [], set(), []
    for m in FILE_HEADER_RE.finditer(diff_text):
        path_b = m.group("b")
        files.append(path_b)
    for f in files:
        if f not in seen:
            uniq.append(f); seen.add(f)
    return uniq
