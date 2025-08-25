import os
import io
import zipfile
import requests
import certifi


def get_verify_path(cfg: dict) -> str:
    pem = (cfg.get("custom_ca_bundle") or "").strip()
    if pem and os.path.exists(pem):
        return pem
    return certifi.where()


def patch_certifi_with_pki_zip(cfg: dict):
    if not cfg.get("enable_pki_zip_patch"):
        return

    url = (cfg.get("pki_zip_url") or "").strip()
    pems = cfg.get("pki_pems") or []

    if not url or not pems:
        return

    try:
        resp = requests.get(url, timeout=60, verify=get_verify_path(cfg))
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            cert_path = certifi.where()
            with open(cert_path, "a", encoding="utf-8") as bundle:
                bundle.write("")  # Optional: placeholder or separator

                for name in pems:
                    content = z.read(name).decode("utf-8")
                    bundle.write(content)
                    bundle.write("")  # Optional: separator between certs
    except Exception as e:
        print(f"[WARN] PKI ZIP patch failed: {e}")
