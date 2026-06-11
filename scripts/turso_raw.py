"""Turso HTTP API 직접 호출 — 에러 응답 원문 확인."""
import os
import sys
import io
import json
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

url = "https://charging-albert64488.aws-ap-northeast-1.turso.io/v2/pipeline"
token = os.environ["TURSO_AUTH_TOKEN"]
body = json.dumps({
    "requests": [
        {"type": "execute", "stmt": {"sql": "select 1"}},
        {"type": "close"},
    ]
}).encode()

req = urllib.request.Request(url, data=body, method="POST")
req.add_header("Authorization", f"Bearer {token}")
req.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print("HTTP", r.status)
        print(r.read().decode()[:2000])
except urllib.error.HTTPError as e:
    print("HTTPError", e.code)
    print(e.read().decode()[:2000])
except Exception as e:
    print("ERR", type(e).__name__, str(e)[:500])
