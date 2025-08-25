# Extracted from ui 12.py

def github_api_base_from_host(host: str) -> str:
    return "https://api.github.com" if host.lower() == "github.com" else f"https://{host}/api/v3"



