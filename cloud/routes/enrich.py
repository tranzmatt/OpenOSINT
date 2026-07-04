"""POST /v1/enrich — run an OSINT tool against a target."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cloud import db, polar, rate_limit, tools
from cloud.auth import get_customer
from cloud.config import CHECKOUT_URLS, TOOL_TIMEOUT_SECONDS
from cloud.key_sources import (
    MissingCredentialError,
    get_credit_cost,
    is_platform_pool_tool,
    resolve_key,
)

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

    # Resolve upstream key before any credit touch — 422 on missing tenant key
    try:
        api_key = await resolve_key(body.tool, customer.api_key)
    except MissingCredentialError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 429 — burst smoothing on shared platform-pool keys (not the spend cap)
    if is_platform_pool_tool(body.tool) and not rate_limit.platform_pool_limiter.allow(
        f"{customer.api_key}:{body.tool}"
    ):
        raise HTTPException(
            status_code=429,
            detail=f"Too many '{body.tool}' requests. Please slow down and try again shortly.",
        )

    cost = get_credit_cost(body.tool)

    # 402 — fast pre-check (avoids a DB round-trip for obviously empty accounts)
    if customer.credits < cost:
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

    # Atomically deduct `cost` credits (guards against concurrent exhaustion)
    new_credits = await db.decrement_credits(customer.api_key, cost)
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


def _raise_402(plan: str) -> None:
    checkout_url = CHECKOUT_URLS.get(plan) or CHECKOUT_URLS.get("payg", "")
    raise HTTPException(
        status_code=402,
        detail={"message": "No credits remaining.", "checkout_url": checkout_url},
    )
