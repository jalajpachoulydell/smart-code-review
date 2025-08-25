
import os
import json

STORE_DIR = os.path.join(os.getcwd(), "pr-code-review")
INDEX_PATH = os.path.join(STORE_DIR, "index.json")

def ensure_store_dir():
    os.makedirs(STORE_DIR, exist_ok=True)
    if not os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump({"items": []}, f, ensure_ascii=False, indent=2)

def load_index():
    ensure_store_dir()
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": []}

def save_index(index_obj):
    ensure_store_dir()
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index_obj, f, ensure_ascii=False, indent=2)
