"""POST /v1/polar/webhook — handle Polar.sh webhook events."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from cloud import db, polar
from cloud.config import BENEFIT_PLAN_MAP, PLAN_CREDITS, POLAR_WEBHOOK_SECRET, SUBSCRIPTION_PLAN_MAP

logger = logging.getLogger(__name__)

router = APIRouter()


# ── event handlers ────────────────────────────────────────────────────────────

async def _handle_benefit_grant(data: dict) -> None:
    """
    Handle benefit_grant.created and benefit_grant.updated.

    Fetches the full license key from the Polar license-keys API using the
    license_key_id in the grant.  The full key becomes the customer's
    X-API-Key — it is never derived from display_key.

    Primary payload path: data.properties.license_key_id
    Fallbacks:           data.license_key_id
                         data.properties.license_key.id

    benefit_grant.created fires only AFTER payment is confirmed.
    """
    # Log once so we can confirm the real payload paths on first live event.
    logger.info("benefit_grant payload: %s", data)

    customer_id = data.get("customer_id", "")
    benefit_id  = data.get("benefit_id", "")
    properties  = data.get("properties") or {}

    # Extract license_key_id defensively across known Polar schema paths.
    license_key_id = (
        properties.get("license_key_id")        # primary: data.properties.license_key_id
        or data.get("license_key_id")            # fallback: data.license_key_id
    )
    if not license_key_id:
        lk = properties.get("license_key")
        if isinstance(lk, dict):
            license_key_id = lk.get("id", "")   # last resort: data.properties.license_key.id

    if not license_key_id:
        logger.error(
            "benefit_grant missing license_key_id — "
            "customer_id=%s benefit_id=%s properties_keys=%s",
            customer_id,
            benefit_id,
            list(properties.keys()),
        )
        return

    # Fetch the full key from Polar — do NOT fall back to display_key on failure.
    api_key = await polar.fetch_license_key(license_key_id)
    if not api_key:
        logger.error(
            "fetch_license_key failed for id=%s customer_id=%s — skipping upsert",
            license_key_id,
            customer_id,
        )
        return

    plan    = BENEFIT_PLAN_MAP.get(benefit_id, "payg")
    credits = PLAN_CREDITS.get(plan, PLAN_CREDITS["payg"])

    await db.upsert_customer(
        api_key=api_key,
        polar_customer_id=customer_id,
        plan=plan,
        credits=credits,
    )
    logger.info(
        "Customer upserted from benefit_grant: plan=%s credits=%d", plan, credits
    )

    # Opportunistically complete the checkout.updated <-> benefit_grant
    # rendezvous: fills in users.customer_api_key if checkout.updated already
    # recorded polar_customer_id for some user. No-op otherwise.
    if customer_id:
        await db.link_customer_api_key_by_polar_id(customer_id, api_key)
    else:
        logger.warning("benefit_grant: no customer_id, skipping api_key link")


async def _handle_benefit_revoke(data: dict) -> None:
    """Zero credits when a license key benefit is revoked."""
    customer_id = data.get("customer_id", "")
    if customer_id:
        await db.zero_credits_by_polar_id(customer_id)
        logger.info(
            "Credits zeroed for polar_customer_id=%s (benefit revoked)", customer_id
        )


async def _handle_subscription_update(data: dict) -> None:
    """
    Refill credits when a subscription renews.

    Polar fires subscription.updated when the subscription's billing period
    advances.  We gate on status == "active" and derive the plan from the
    product ID mapping in config.

    ⚠️  Verify the renewal event name and that product.id is present in the
        subscription.updated payload via Polar dashboard → Send test event.
    """
    if data.get("status") != "active":
        return

    customer_id = data.get("customer_id", "")
    product     = data.get("product") or {}
    product_id  = product.get("id", "")
    plan        = SUBSCRIPTION_PLAN_MAP.get(product_id, "")

    if not plan:
        logger.warning(
            "subscription.updated: unknown product_id=%s — "
            "set POLAR_PRODUCT_ID_STARTER / POLAR_PRODUCT_ID_PRO env vars",
            product_id,
        )
        return

    credits = PLAN_CREDITS.get(plan, 0)
    if customer_id and credits:
        await db.refill_credits_by_polar_id(customer_id, credits)
        logger.info(
            "Credits refilled: polar_customer_id=%s plan=%s credits=%d",
            customer_id,
            plan,
            credits,
        )


async def _handle_checkout_updated(data: dict) -> None:
    """
    Link the OAuth-login user to their Polar customer on checkout completion.

    reference_id (== users.id, passed as a ?reference_id= query param on the
    hosted checkout link) arrives as a string in data.metadata.reference_id.
    Checkouts made without our dashboard link carry no reference_id — those
    are skipped silently, not treated as an error.
    """
    logger.info("checkout.updated payload: %s", data)

    if data.get("status") != polar.COMPLETED_CHECKOUT_STATUS:
        return

    customer_id = data.get("customer_id", "")
    reference_id_raw = (data.get("metadata") or {}).get("reference_id")
    if not customer_id:
        logger.warning("checkout.updated: no customer_id, skipping link")
        return
    if reference_id_raw is None:
        logger.warning("checkout.updated: no reference_id, skipping link")
        return
    try:
        user_id = int(reference_id_raw)
    except (TypeError, ValueError):
        logger.warning(
            "checkout.updated: non-numeric reference_id=%r, skipping link",
            reference_id_raw,
        )
        return

    await db.link_checkout_to_user(user_id, customer_id)
    logger.info(
        "Linked user_id=%d to polar_customer_id=%s via checkout.updated",
        user_id,
        customer_id,
    )


# ── dispatch table ────────────────────────────────────────────────────────────

_HANDLERS = {
    polar.EVT_BENEFIT_GRANT_CREATED: _handle_benefit_grant,
    polar.EVT_BENEFIT_GRANT_UPDATED: _handle_benefit_grant,
    polar.EVT_BENEFIT_GRANT_REVOKED: _handle_benefit_revoke,
    polar.EVT_SUBSCRIPTION_UPDATED:  _handle_subscription_update,
    polar.EVT_CHECKOUT_UPDATED:      _handle_checkout_updated,
}


# ── route ─────────────────────────────────────────────────────────────────────

@router.post("/polar/webhook")
async def polar_webhook(request: Request) -> JSONResponse:
    body = await request.body()

    msg_id        = request.headers.get("webhook-id", "")
    msg_timestamp = request.headers.get("webhook-timestamp", "")
    msg_signature = request.headers.get("webhook-signature", "")

    if not polar.verify_webhook_signature(
        body, msg_id, msg_timestamp, msg_signature, POLAR_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload    = json.loads(body)
    event_type = payload.get("type", "")
    # Standard Webhooks guarantees webhook-id is unique per message delivery.
    event_id   = msg_id or payload.get("id", "")

    if not event_id:
        logger.error("Webhook delivered with no event ID — cannot guarantee idempotency")
        return JSONResponse({"status": "error", "detail": "missing event id"}, status_code=400)

    if await db.is_event_processed(event_id):
        logger.info("Duplicate webhook (event_id=%s) — no-op", event_id)
        return JSONResponse({"status": "already_processed"})

    handler = _HANDLERS.get(event_type)
    if handler:
        await handler(payload.get("data") or {})
    else:
        logger.info("Unhandled Polar event type: %s", event_type)

    await db.mark_event_processed(event_id)
    return JSONResponse({"status": "ok"})
