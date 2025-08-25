from openai import OpenAI
import httpx
from .tls import get_verify_path, patch_certifi_with_pki_zip


def get_gateway_token(cfg: dict) -> str:
    mode = (cfg.get("token_mode") or "preissued").lower()
    if mode == "preissued":
        tok = (cfg.get("aia_access_token") or "").strip()
        if not tok:
            raise RuntimeError("token_mode is 'preissued' but aia_access_token is empty.")
        return tok
    elif mode == "aia_auth":
        try:
            from aia_auth import auth
        except Exception as e:
            raise RuntimeError(
                "token_mode is 'aia_auth' but aia_auth is not installed.\n"
                "pip install aia-auth-client==0.0.6 --trusted-host artifacts.dell.com "
                "--extra-index https://artifacts.dell.com/artifactory/api/pypi/agtsdk-1007569-pypi-prd-local/simple"
            ) from e

        cid = (cfg.get("client_id") or "").strip()
        csec = (cfg.get("client_secret") or "").strip()
        if not cid or not csec:
            raise RuntimeError("CLIENT_ID/CLIENT_SECRET are required in 'aia_auth' mode.")

        try:
            token_resp = auth.client_credentials(cid, csec)
            if not getattr(token_resp, "token", None):
                raise RuntimeError(f"aia_auth returned no token: {token_resp}")
            return token_resp.token
        except Exception as e:
            raise RuntimeError(f"client_credentials failed: {e}") from e
    else:
        raise RuntimeError(f"Unknown token_mode: {mode}")


def make_client(cfg: dict):
    patch_certifi_with_pki_zip(cfg)
    verify = get_verify_path(cfg)
    token = get_gateway_token(cfg)
    http_client = httpx.Client(verify=verify)
    client = OpenAI(
        base_url=cfg["gateway_base"].rstrip("/"),
        http_client=http_client,
        api_key=token,
    )
    return client
