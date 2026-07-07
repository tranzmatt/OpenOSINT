from __future__ import annotations
import base64
import hashlib
import hmac
import sys
import time
import requests

_KEY = b"isolation-test-key-32-bytes!!!!"
SECRET = "whsec_" + base64.b64encode(_KEY).decode()
BODY = b'{"type":"checkout.updated","data":{"customer_id":"cust_isolation_test"}}'
MSG_ID = "msg_isolation_test_001"

def build_headers(body: bytes) -> dict[str, str]:
    msg_timestamp = str(int(time.time()))
    signed_content = f"{MSG_ID}.{msg_timestamp}.".encode() + body
    sig = base64.b64encode(hmac.new(_KEY, signed_content, hashlib.sha256).digest()).decode()
    return {
        "Content-Type": "application/json",
        "webhook-id": MSG_ID,
        "webhook-timestamp": msg_timestamp,
        "webhook-signature": f"v1,{sig}",
    }

def post(label: str, url: str) -> None:
    headers = build_headers(BODY)
    print(f"\n--- {label}: {url} ---")
    print(f"body bytes sent ({len(BODY)}): {BODY!r}")
    try:
        resp = requests.post(url, data=BODY, headers=headers, timeout=10)
        print(f"status: {resp.status_code}")
        print(f"response body: {resp.text}")
    except Exception as exc:
        print(f"REQUEST FAILED: {exc!r}")

def main() -> None:
    print(f"POLAR_WEBHOOK_SECRET to use in T1:\n  {SECRET}\n")
    print("Restart T1 with that value set, THEN press Enter to continue.")
    input()
    post("LOCAL", "http://localhost:8000/v1/polar/webhook")
    if len(sys.argv) > 1:
        tunnel_url = sys.argv[1].rstrip("/") + "/v1/polar/webhook"
        post("TUNNEL", tunnel_url)
    else:
        print("\nNo tunnel URL passed.")

if __name__ == "__main__":
    main()
