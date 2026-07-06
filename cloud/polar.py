"""
OpenOSINT Cloud — Polar.sh integration.

Covers two responsibilities:
  1. Webhook signature verification (Standard Webhooks / HMAC-SHA256).
  2. Best-effort usage-event ingestion (analytics telemetry, not billing).

Polar uses the Standard Webhooks spec: https://www.standardwebhooks.com
Headers supplied by Polar on each delivery:
  webhook-id         — unique message ID (use as idempotency key)
  webhook-timestamp  — Unix seconds (string)
  webhook-signature  — space-separated list of "v1,<base64sig>" tokens

Signed content: "{webhook-id}.{webhook-timestamp}.{raw_body}"
Key:            HMAC-SHA256 secret from Polar dashboard (base64 or whsec_ prefixed)

⚠️  Verify the exact signature format by sending a test event from
    Polar dashboard → Developer → Webhooks → Send test event.

Polar webhook event type strings subscribed to in this gateway:
  - benefit_grant.created   (primary: license key granted → create customer)
  - benefit_grant.updated   (key refresh / status change)
  - benefit_grant.revoked   (account deactivated → zero credits)
  - subscription.updated    (renewal → refill credits when status == "active")
  - checkout.updated        (links users.polar_customer_id from
                             data.metadata.reference_id once the checkout
                             reaches COMPLETED_CHECKOUT_STATUS)

⚠️  Verify these string values in Polar dashboard → Developer → Webhooks
    → Send test event → select event type → inspect the "type" field.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time

import httpx

from cloud.config import POLAR_API_BASE

logger = logging.getLogger(__name__)

_TIMESTAMP_TOLERANCE_SECS = 300  # reject webhooks older than 5 minutes

# ── event type constants ──────────────────────────────────────────────────────
# ⚠️  Confirm these against Polar's test-event tool before going live.
EVT_BENEFIT_GRANT_CREATED = "benefit_grant.created"
EVT_BENEFIT_GRANT_UPDATED = "benefit_grant.updated"
EVT_BENEFIT_GRANT_REVOKED = "benefit_grant.revoked"
EVT_SUBSCRIPTION_UPDATED  = "subscription.updated"
EVT_CHECKOUT_UPDATED      = "checkout.updated"

# ⚠️  "succeeded" is Polar's documented terminal/paid checkout status, but
# this has not yet been confirmed against a real completed checkout payload.
# Kept as a single constant so it's a one-line fix if the real value differs.
COMPLETED_CHECKOUT_STATUS = "succeeded"


# ── webhook signature verification ───────────────────────────────────────────

def _decode_secret(secret: str) -> bytes:
    """
    Decode a Polar/Standard Webhooks secret: strip the whsec_ prefix if
    present, then base64-decode. Polar's secrets are always base64 under
    the hood (per the Standard Webhooks reference implementation) — this
    mirrors that exactly rather than guessing between base64 and raw
    bytes, which can silently decode a valid secret into the wrong key.
    """
    if secret.startswith("whsec_"):
        secret = secret[len("whsec_"):]
    # Padding fix: b64decode ignores extra padding, so this is safe even
    # when the secret is already correctly padded.
    return base64.b64decode(secret + "==")


def verify_webhook_signature(
    body: bytes,
    msg_id: str,
    msg_timestamp: str,
    msg_signature: str,
    secret: str,
) -> bool:
    """
    Verify a Polar webhook using Standard Webhooks HMAC-SHA256.
    Returns False (never raises) on any verification failure.
    """
    if not secret:
        logger.error("POLAR_WEBHOOK_SECRET is not set — rejecting all webhooks")
        return False
    try:
        ts = int(msg_timestamp)
        if abs(time.time() - ts) > _TIMESTAMP_TOLERANCE_SECS:
            logger.warning("Webhook timestamp outside ±5 min window: %s", msg_timestamp)
            return False
        key = _decode_secret(secret)
        signed_content = f"{msg_id}.{msg_timestamp}.".encode() + body
        expected = base64.b64encode(
            hmac.new(key, signed_content, hashlib.sha256).digest()
        ).decode()
        # Signature header may carry multiple space-separated tokens
        for token in msg_signature.split():
            if "," in token:
                _, sig_val = token.split(",", 1)
                if hmac.compare_digest(expected, sig_val):
                    return True
        if os.environ.get("POLAR_WEBHOOK_DEBUG"):
            # ponytail: temporary diagnostic, remove once mismatch is root-caused.
            # Logs only length/prefixes — never the secret or full signatures.
            logger.warning(
                "sig debug: key_len=%d expected_prefix=%s recv_prefixes=%s",
                len(key),
                expected[:8],
                [t.split(",", 1)[-1][:8] for t in msg_signature.split() if "," in t],
            )
        logger.warning("Webhook signature mismatch")
        return False
    except Exception as exc:
        logger.error("Webhook signature verification error: %s", exc)
        return False


# ── usage event ingestion ─────────────────────────────────────────────────────

async def send_usage_event(polar_customer_id: str, tool: str) -> None:
    """
    Ingest a usage analytics event to Polar (best-effort telemetry).
    Errors are always swallowed — this must never block or break a paid call.

    ⚠️  Verify the endpoint path against Polar's Events API documentation.
        Current target: POST /v1/events/ingest
    """
    token = os.environ.get("POLAR_TOKEN", "")
    if not token or not polar_customer_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{POLAR_API_BASE}/v1/events/ingest",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "name": "api_call",
                    "customer_id": polar_customer_id,
                    "properties": {"tool": tool},
                },
            )
    except Exception as exc:
        logger.debug("Polar usage event failed (swallowed): %s", exc)


async def fetch_license_key(license_key_id: str) -> str | None:
    """
    Fetch the full license key string from the Polar license-keys API.

    Returns the `key` field from the response, or None on any error.
    Errors are logged but never raised — the webhook handler must decide
    whether to proceed or skip.
    """
    token = os.environ.get("POLAR_TOKEN", "")
    if not token or not license_key_id:
        logger.error(
            "fetch_license_key: POLAR_TOKEN not set or license_key_id empty (id=%r)",
            license_key_id,
        )
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{POLAR_API_BASE}/v1/license-keys/{license_key_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            logger.error(
                "Polar license-keys API returned HTTP %d for id=%s",
                resp.status_code,
                license_key_id,
            )
            return None
        key = resp.json().get("key", "")
        if not key:
            logger.error(
                "Polar license-keys response missing 'key' field for id=%s: %s",
                license_key_id,
                resp.text[:200],
            )
            return None
        return key
    except Exception as exc:
        logger.error("Failed to fetch license key id=%s: %s", license_key_id, exc)
        return None
