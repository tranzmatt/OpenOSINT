"""POST /v1/enrich — run an OSINT tool against a target."""
from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cloud import db, keys, polar, tools
from cloud.auth import get_customer
from cloud.config import CHECKOUT_URLS, TOOL_TIMEOUT_SECONDS
from cloud.key_sources import TOOL_KEY_CONFIG, KeySource

logger = logging.getLogger(__name__)

router = APIRouter()

_ERROR_PREFIXES = ("Scan error", "Internal error", "Error:")


class EnrichRequest(BaseModel):
    tool: str
    target: str


class EnrichResponse(BaseModel):
    tool: str
    target: str
    timestamp: str
    results: list[str]
    error: str | None
    credits_left: int


@router.post("/enrich", response_model=EnrichResponse)
async def enrich(
    body: EnrichRequest,
    customer: db.Customer = Depends(get_customer),
) -> EnrichResponse:
    # 400 — tool not in allow-list (no credit touch)
    if body.tool not in tools.ALLOW_LIST:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Tool '{body.tool}' is not available in v1.  "
                f"Available: {sorted(tools.ALLOW_LIST)}"
            ),
        )

    # Resolve upstream key before any credit touch — 422 on missing customer key
    api_key = await _resolve_key(body.tool, customer)

    # 402 — fast pre-check (avoids a DB round-trip for obviously empty accounts)
    if customer.credits <= 0:
        _raise_402(customer.plan)

    # Run the tool first; we only charge on a successful result
    try:
        result = await asyncio.wait_for(
            tools.dispatch(body.tool, body.target, api_key=api_key),
            timeout=float(TOOL_TIMEOUT_SECONDS),
        )
    except asyncio.TimeoutError:
        logger.warning("Tool %s timed out (target=%s)", body.tool, body.target)
        raise HTTPException(
            status_code=504,
            detail=f"Tool '{body.tool}' exceeded the {TOOL_TIMEOUT_SECONDS} s timeout",
        )

    # No charge when the tool returned an upstream error
    first_line = result["results"][0] if result["results"] else (result.get("error") or "")
    if any(first_line.startswith(p) for p in _ERROR_PREFIXES):
        return EnrichResponse(
            tool=result["tool"],
            target=result["target"],
            timestamp=result["timestamp"],
            results=result["results"],
            error=result["error"],
            credits_left=customer.credits,
        )

    # Atomically deduct one credit (guards against concurrent exhaustion)
    new_credits = await db.decrement_credits(customer.api_key)
    if new_credits is None:
        # Race: a concurrent request drained the last credit between pre-check and now
        _raise_402(customer.plan)

    # Fire-and-forget Polar usage telemetry — errors are swallowed in polar.py
    if customer.polar_customer_id:
        asyncio.create_task(
            polar.send_usage_event(customer.polar_customer_id, body.tool)
        )

    return EnrichResponse(
        tool=result["tool"],
        target=result["target"],
        timestamp=result["timestamp"],
        results=result["results"],
        error=result["error"],
        credits_left=new_credits,
    )


async def _resolve_key(tool: str, customer: db.Customer) -> str | None:
    """Return the upstream API key for tool, per TOOL_KEY_CONFIG."""
    cfg = TOOL_KEY_CONFIG.get(tool)
    if cfg is None or cfg.source == KeySource.none:
        return None

    if cfg.source == KeySource.platform:
        return os.environ.get(cfg.env_var, "") or None

    # tenant or tenant_optional — look up from the customer's encrypted store
    stored = await keys.get_key(customer.api_key, cfg.provider)

    if cfg.source == KeySource.tenant and stored is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Tool '{tool}' requires a '{cfg.provider}' API key. "
                f"Add it with: POST /v1/keys "
                f'{{\"provider\": \"{cfg.provider}\", \"secret\": \"your_key\"}}'
            ),
        )

    return stored  # None is valid for tenant_optional when key is absent


def _raise_402(plan: str) -> None:
    checkout_url = CHECKOUT_URLS.get(plan) or CHECKOUT_URLS.get("payg", "")
    raise HTTPException(
        status_code=402,
        detail={"message": "No credits remaining.", "checkout_url": checkout_url},
    )
