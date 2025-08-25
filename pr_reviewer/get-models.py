# list_models_preissued.py
import os
import sys
import certifi
import httpx
from openai import OpenAI

# ---- CONFIG (edit these two or use env vars) ----
GATEWAY_BASE = os.environ.get("AIA_GATEWAY_BASE", "https://aia.gateway.dell.com/genai/dev/v1")
AIA_ACCESS_TOKEN = os.environ.get("AIA_ACCESS_TOKEN", "eyJhbGciOiJSUzI1NiIsImtpZCI6ImF0LTE2MDk1NTkzNDAiLCJ0eXAiOiJKV1QifQ.eyJpYXQiOjE3NTU4Njc1ODIsImp0aSI6IjdjZmM1OGFjLWY0MGEtNGFhZi1iOGQ1LTU2NjkxMDE0MTJiMCIsInN1YiI6IkphbGFqLlBhY2hvdWx5QGRlbGwuY29tIiwiY2xpZW50X2lkIjoiYTE0YjQwODYtZDY4Mi00YjgxLWEwN2MtNTdkYTUxNDM3ZWZlIiwicHJvZmlsZWlkIjoiYjcxOTA4ODAtODU1Yy00OWU2LWFiZmUtNGRhOTk2ZDYzYTk3IiwiQURCREciOiIxMzY3NTA1IiwiYXV0aHNyYyI6IkFEIiwiQURVTiI6IkphbGFqX1BhY2hvdWx5IiwiQURET00iOiJBU0lBLVBBQ0lGSUMiLCJQWVNJRCI6ImFjOGMwMzE2LWEzYmYtNDMxZC1iNjkwLTJiOWJiODE4ZTk3NCIsIkVYVElEUCI6IlRydWUiLCJncm91cHMiOlsiT2JzZXJ2YWJpbGl0eUFjY2Vzc18xMDA1NzkxIiwiZHRjY3AtZ3JvdXBzLWRldiIsImR0Y2NwLXJvbGVzLWRldiIsImR0Y2NwLXVzZXJzLWRldiIsImR0Y2NwLXNlY3VyaXR5LXNlcnZpY2UtZGV2IiwiaXNnZWRnZS5jaGVja21hcngucmV2IiwiRENTR1NwbHVua0J1c2luZXNzVXNlcnMiLCJPYnNlcnZhYmlsaXR5QWNjZXNzXzEwMDU3ODkiLCJPYnNlcnZhYmlsaXR5QWNjZXNzXzEwMDIwNjgiLCJPYnNlcnZhYmlsaXR5QWNjZXNzXzIwMjIzIiwiT2JzZXJ2YWJpbGl0eUFjY2Vzc181MDg2IiwiTWFpbGJveF9Vc2Vyc19ZYXNoIiwiSVNHLUVuZ2luZWVyaW5nLVNsYWNrV29ya3NwYWNlIiwiWm9vbU1pZ3JhdGlvbkV4ZW1wdCIsIkFkb2JlX1NpZ24iLCJBbGwgRGVsbCBUTXMgRXhjIFF1ZWJlYyAzIiwiTWlyb19BdXRoX0ZyZWVfUmVzdHJpY3Rpb24iLCJPYnNlcnZhYmlsaXR5ICYgTW9uaXRvcmluZyAtIFRyYWNpbmcgR2xvYmFsIFVzZXJzIiwiaXNnX2FjY2Vzc19jb2RlaXVtIiwiQlRfUE1XX0FnZW50X0RlcGxveW1lbnQiLCJQU1RfU2Nhbm5lciIsIkFsbE9yZ1RvYXN0Tm90aWZpY2F0aW9uIiwiQlRfUE1XX1Bvd2VyVXNlciIsIlBGX3JlZGlyZWN0X3RvX0F6dXJlX0F1dGgiLCJBWkRfRVhPTF9BUEFDX01vYmlsZSIsIlBhbmRPX1BsdXJhbHNpZ2h0IiwiSVNHX0JUX0RlcGxveW1lbnQiLCJEZWxsRWRnZSBBbGwgQXV0byIsIk1TQ2xvdWRCdWlsZGVyUGlsb3QiLCJHUE9fRXhjZXB0aW9uT3V0bG9va0ZvcldpbmRvd3MiLCJTR19Pc2pEdGNEZXZlbG9wZXJzIiwiS0ZNRW5hYmxlZENvbmZpZ19ERUxMIiwiRXhjbHVkZVRlbXBMYXJEZXBsb3ltZW50IiwiVHJhY2luZ0R5bmF0cmFjZVJPIiwiMDM2NV9ab29tX091dGxvb2tfQWRkSW4iLCJQTyBJbmRpdmlkdWFsIENvbnRyaWJ1dG9ycyIsIldlbGNvbWVHdWlkZSIsIkVTR1RfU3BvbnNvcnMiLCJJRU9HaXRodWIiLCJNU0ROX1Zpc3VhbF9TdHVkaW9fUHJvIiwiU2xhY2tfQXV0aF9TdG9yYWdlIiwiU1NMLVZQTi1Ob24tVFBBIiwiU0lTX0FsbCIsIlRyYXZlbCBJTkRJQSBERUxMIEFsbCBQZXJtIFN0YWZmIERMIiwiRVNSU1VzZXJzIiwiR1BfR2xvYmFsX091dGxvb2tfRGlzYWJsZV9QU1QiLCJSU0FPbmVUb2tlblVzZXJzUFJEIiwiRGVsbF9NZW1iZXJzX0Z1bGx0aW1lXzIiLCJEZWxsX01lbWJlcnNfMiIsIkluZGlhX0J1c2luZXNzX0Rpc3RybyIsIkRDU0dTcGx1bmtVc2VycyIsIkVTR1RfQWR2b2NhdGVzIiwiRVNHVF9Vc2VycyIsIkRlbGxfSW5kaWFfVGVhbV9NZW1iZXJzMiIsIkNNU19BbGxEZWxsRW1wbG95ZWVzNSIsIkRsaXN0X0dPX0dyb3VwMyIsIkFQSl9BbGxFbXBsb3llZXNfMyJdLCJzdWJ0eXBlIjoidXNlciIsInR0eSI6ImF0Iiwic2NvcGUiOlsiYWlhLWdhdGV3YXkuZmluZXR1bmluZyIsImFpYS1nYXRld2F5LmdlbmFpLmRldiJdLCJhdWQiOiJhaWEtZ2F0ZXdheSIsIm5iZiI6MTc1NTg2NzU4MywiZXhwIjoxNzU1ODY5MzgzLCJpc3MiOiJodHRwOi8vd3d3LmRlbGwuY29tL2lkZW50aXR5In0.a-RXXkMMqBVjiVF9LE6bPwU9qbjZ_lsUanOnrIJSjBLi2tQ_N3yZ20AWzC5XeMhpormic8Ai15EyJD48PCJdo0aUCFCUnnl1mW5S0rjFrl169-3QF6E-dnk9kTdpmJWHZuPOja8IBnMwOuiwkjKh6PWqND4F_yE65td2TQT6IfjEmmad5KH4EUpk295tHnhp34IhfDFDBRAn1nXILBGYoT4CjCqEquhUF778KmkOy7RKfwvuAqnaGkrLAmeUBXi568jMqyUew18hs76Ka6TMCVU6U5CpC5moVEFq3R6ya536fE073ZCYRG8vO71EcbrmQen4GZDQq7EiUu0am6YHJg")
# -------------------------------------------------

if not AIA_ACCESS_TOKEN or AIA_ACCESS_TOKEN.startswith("<PUT-"):
    print("ERROR: Set AIA_ACCESS_TOKEN (env var or inline string).", file=sys.stderr)
    sys.exit(1)


def main():
    # use system CAs (swap to your corporate bundle if needed)
    http_client = httpx.Client(verify=certifi.where(), timeout=30.0)

    # OpenAI-compatible client pointed at the Dell AIA gateway
    client = OpenAI(
        base_url=GATEWAY_BASE.rstrip("/"),
        api_key=AIA_ACCESS_TOKEN,  # preissued bearer token
        http_client=http_client,
    )

    try:
        models = client.models.list()
    except Exception as e:
        print(f"Failed to list models: {e}", file=sys.stderr)
        sys.exit(2)

    # Pretty print
    data = getattr(models, "data", None) or []
    if not data:
        print("No models returned.")
        return

    print(f"Found {len(data)} model(s):")
    for m in data:
        # Typical fields: id, created, owned_by, object
        mid = getattr(m, "id", None) or "<unknown-id>"
        owned_by = getattr(m, "owned_by", None)
        created = getattr(m, "created", None)
        print(
            f" - {mid}" + (f"  (owner: {owned_by})" if owned_by else "") + (f"  created: {created}" if created else ""))


if __name__ == "__main__":
    main()