"""
OpenOSINT Cloud — environment configuration.

All secrets and tunable values come from environment variables.
Never hardcode values here.
"""
from __future__ import annotations

import os

# ── Polar.sh ──────────────────────────────────────────────────────────────────
POLAR_TOKEN          = os.environ.get("POLAR_TOKEN", "")
POLAR_WEBHOOK_SECRET = os.environ.get("POLAR_WEBHOOK_SECRET", "")
POLAR_API_BASE       = os.environ.get("POLAR_API_BASE", "https://api.polar.sh")

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ── Limits ────────────────────────────────────────────────────────────────────
# Heroku's HTTP router kills requests that don't return the first byte within
# 30 s (H12 error).  Keep this strictly below that limit.
TOOL_TIMEOUT_SECONDS: int = 25

# ── Per-tool credit cost ───────────────────────────────────────────────────────
# Platform-pool tools cost upstream_cost + margin; the single named constant
# below is the only place Shodan's cost needs tuning (referenced once, in
# cloud/key_sources.TOOL_KEY_CONFIG). Placeholder until real usage data comes in.
SHODAN_CREDIT_COST: int = 4

# ── Platform-pool burst limiter ────────────────────────────────────────────────
# Burst smoothing only, not the spend cap (that's the atomic Postgres credit
# decrement in cloud/db.py). Keeps one tenant from hammering a shared platform
# key (Shodan, IP2Location) with rapid-fire requests.
PLATFORM_BURST_WINDOW_SECS: float = float(os.environ.get("PLATFORM_BURST_WINDOW_SECS", "60"))
PLATFORM_BURST_MAX_CALLS: int = int(os.environ.get("PLATFORM_BURST_MAX_CALLS", "20"))

# ── Plan definitions ──────────────────────────────────────────────────────────
# Credits granted when a customer purchases / activates a plan.
PLAN_CREDITS: dict[str, int] = {
    "payg":    100,   # $10 pack → 100 calls @ $0.10/call
    "starter": 1_000, # $19/mo
    "pro":     5_000, # $49/mo
}

# ── Polar hosted checkout URLs ────────────────────────────────────────────────
# Set these after creating products in your Polar dashboard.
CHECKOUT_URLS: dict[str, str] = {
    "payg":    os.environ.get("POLAR_CHECKOUT_PAYG", ""),
    "starter": os.environ.get("POLAR_CHECKOUT_STARTER", ""),
    "pro":     os.environ.get("POLAR_CHECKOUT_PRO", ""),
}

# ── Benefit ID → plan name mapping ───────────────────────────────────────────
# Copy the benefit IDs from Polar dashboard → Products → <product> → Benefits.
# Used by the webhook handler to know which plan to grant on benefit_grant.created.
BENEFIT_PLAN_MAP: dict[str, str] = {
    val: plan
    for env_key, plan in [
        ("POLAR_BENEFIT_ID_PAYG",    "payg"),
        ("POLAR_BENEFIT_ID_STARTER", "starter"),
        ("POLAR_BENEFIT_ID_PRO",     "pro"),
    ]
    if (val := os.environ.get(env_key, ""))
}

# ── Product ID → plan name mapping ───────────────────────────────────────────
# Used by the webhook handler to refill credits on subscription renewal.
# Copy from Polar dashboard → Products → <product> → ID.
SUBSCRIPTION_PLAN_MAP: dict[str, str] = {
    val: plan
    for env_key, plan in [
        ("POLAR_PRODUCT_ID_STARTER", "starter"),
        ("POLAR_PRODUCT_ID_PRO",     "pro"),
    ]
    if (val := os.environ.get(env_key, ""))
}
