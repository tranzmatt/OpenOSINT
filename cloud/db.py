"""
OpenOSINT Cloud — database layer.

Uses asyncpg when DATABASE_URL is set (Heroku Postgres in production).
Falls back to an in-memory store when DATABASE_URL is absent (tests / local dev).

Public API
----------
init_pool()                              — call on app startup
close_pool()                             — call on app shutdown
get_customer(api_key)        → Customer | None
decrement_credits(api_key, cost=1) → int | None   (None = not enough credits)
upsert_customer(...)                     — create or replace
zero_credits_by_polar_id(...)
refill_credits_by_polar_id(...)
is_event_processed(event_id) → bool
mark_event_processed(event_id)
get_or_create_user(provider, provider_user_id, email) → User
get_user(user_id)            → User | None
"""
from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

try:
    import asyncpg  # type: ignore
    _HAS_ASYNCPG = True
except ImportError:
    _HAS_ASYNCPG = False

_pool: Any = None  # asyncpg.Pool or None


# ── domain model ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Customer:
    api_key: str
    polar_customer_id: str | None
    credits: int
    plan: str
    created_at: datetime = dataclasses.field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True)
class User:
    """An OAuth login identity (GitHub / Google). Web-dashboard login only —
    X-API-Key / MCP bearer auth never reads this table."""
    id: int
    provider: str
    provider_user_id: str
    email: str | None
    polar_customer_id: str | None
    customer_api_key: str | None
    created_at: datetime = dataclasses.field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ── in-memory store (tests only) ─────────────────────────────────────────────

_MEMORY_CUSTOMERS: dict[str, Customer] = {}   # api_key → Customer
_MEMORY_BY_POLAR_ID: dict[str, str] = {}       # polar_customer_id → api_key
_MEMORY_EVENTS: set[str] = set()

_MEMORY_USERS: dict[int, User] = {}                        # id → User
_MEMORY_USERS_BY_IDENTITY: dict[tuple[str, str], int] = {}  # (provider, provider_user_id) → id
_next_user_id = 1


def _is_memory_mode() -> bool:
    return _pool is None


# ── pool lifecycle ────────────────────────────────────────────────────────────

async def init_pool() -> None:
    global _pool
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        logger.warning("DATABASE_URL not set — using in-memory store (tests / local dev only)")
        return
    if not _HAS_ASYNCPG:
        raise RuntimeError(
            "asyncpg is required for production.  "
            "Add asyncpg>=0.29.0 to requirements.txt and redeploy."
        )
    # Heroku Postgres exposes a postgres:// DSN; asyncpg requires postgresql://
    dsn = database_url.replace("postgres://", "postgresql://", 1)
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    logger.info("Database pool connected")


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ── read ──────────────────────────────────────────────────────────────────────

async def get_customer(api_key: str) -> Customer | None:
    if _is_memory_mode():
        return _MEMORY_CUSTOMERS.get(api_key)
    row = await _pool.fetchrow(
        "SELECT api_key, polar_customer_id, credits, plan, created_at "
        "FROM customers WHERE api_key = $1",
        api_key,
    )
    if row is None:
        return None
    return Customer(
        api_key=row["api_key"],
        polar_customer_id=row["polar_customer_id"],
        credits=row["credits"],
        plan=row["plan"],
        created_at=row["created_at"],
    )


# ── write ─────────────────────────────────────────────────────────────────────

async def decrement_credits(api_key: str, cost: int = 1) -> int | None:
    """
    Atomically subtract `cost` credits if credits >= cost.

    Returns the new credit balance on success.
    Returns None if there weren't enough credits (caller should respond 402).
    """
    if _is_memory_mode():
        current = _MEMORY_CUSTOMERS.get(api_key)
        if current is None or current.credits < cost:
            return None
        updated = dataclasses.replace(current, credits=current.credits - cost)
        _MEMORY_CUSTOMERS[api_key] = updated
        return updated.credits
    row = await _pool.fetchrow(
        "UPDATE customers SET credits = credits - $2 "
        "WHERE api_key = $1 AND credits >= $2 "
        "RETURNING credits",
        api_key,
        cost,
    )
    return row["credits"] if row else None


async def upsert_customer(
    api_key: str,
    polar_customer_id: str | None,
    plan: str,
    credits: int,
) -> None:
    """Create or fully replace a customer's credits and plan (called by webhook)."""
    new = Customer(
        api_key=api_key,
        polar_customer_id=polar_customer_id,
        credits=credits,
        plan=plan,
    )
    if _is_memory_mode():
        if polar_customer_id and polar_customer_id in _MEMORY_BY_POLAR_ID:
            # Remove stale api_key entry so the lookup index stays consistent.
            old_key = _MEMORY_BY_POLAR_ID[polar_customer_id]
            if old_key != api_key:
                _MEMORY_CUSTOMERS.pop(old_key, None)
        _MEMORY_CUSTOMERS[api_key] = new
        if polar_customer_id:
            _MEMORY_BY_POLAR_ID[polar_customer_id] = api_key
        return
    await _pool.execute(
        """
        INSERT INTO customers (api_key, polar_customer_id, credits, plan)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (polar_customer_id) WHERE polar_customer_id IS NOT NULL DO UPDATE
            SET api_key = EXCLUDED.api_key,
                credits = EXCLUDED.credits,
                plan    = EXCLUDED.plan
        """,
        api_key,
        polar_customer_id,
        credits,
        plan,
    )


async def zero_credits_by_polar_id(polar_customer_id: str) -> None:
    """Zero out a customer's credits when their benefit is revoked."""
    if _is_memory_mode():
        api_key = _MEMORY_BY_POLAR_ID.get(polar_customer_id)
        if api_key and api_key in _MEMORY_CUSTOMERS:
            _MEMORY_CUSTOMERS[api_key] = dataclasses.replace(
                _MEMORY_CUSTOMERS[api_key], credits=0
            )
        return
    await _pool.execute(
        "UPDATE customers SET credits = 0 WHERE polar_customer_id = $1",
        polar_customer_id,
    )


async def refill_credits_by_polar_id(polar_customer_id: str, credits: int) -> None:
    """Reset credit balance to `credits` — called on subscription renewal."""
    if _is_memory_mode():
        api_key = _MEMORY_BY_POLAR_ID.get(polar_customer_id)
        if api_key and api_key in _MEMORY_CUSTOMERS:
            _MEMORY_CUSTOMERS[api_key] = dataclasses.replace(
                _MEMORY_CUSTOMERS[api_key], credits=credits
            )
        return
    await _pool.execute(
        "UPDATE customers SET credits = $2 WHERE polar_customer_id = $1",
        polar_customer_id,
        credits,
    )


# ── idempotency ───────────────────────────────────────────────────────────────

async def is_event_processed(event_id: str) -> bool:
    if _is_memory_mode():
        return event_id in _MEMORY_EVENTS
    row = await _pool.fetchrow(
        "SELECT event_id FROM processed_events WHERE event_id = $1",
        event_id,
    )
    return row is not None


async def mark_event_processed(event_id: str) -> None:
    if _is_memory_mode():
        _MEMORY_EVENTS.add(event_id)
        return
    await _pool.execute(
        "INSERT INTO processed_events (event_id) VALUES ($1) ON CONFLICT DO NOTHING",
        event_id,
    )


# ── users (OAuth login identities) ────────────────────────────────────────────

async def get_or_create_user(provider: str, provider_user_id: str, email: str | None) -> User:
    """Find the user for (provider, provider_user_id), creating one on first login.

    Refreshes `email` on every login without clobbering a previously stored
    address when the provider returns none this time (e.g. a private GitHub
    email). No implicit link to any `customers` row — that only happens via
    the checkout.updated / benefit_grant.created rendezvous (a later commit).
    """
    if _is_memory_mode():
        global _next_user_id
        identity = (provider, provider_user_id)
        existing_id = _MEMORY_USERS_BY_IDENTITY.get(identity)
        if existing_id is not None:
            current = _MEMORY_USERS[existing_id]
            updated = dataclasses.replace(current, email=email or current.email)
            _MEMORY_USERS[existing_id] = updated
            return updated
        user = User(
            id=_next_user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            email=email,
            polar_customer_id=None,
            customer_api_key=None,
        )
        _MEMORY_USERS[user.id] = user
        _MEMORY_USERS_BY_IDENTITY[identity] = user.id
        _next_user_id += 1
        return user

    row = await _pool.fetchrow(
        """
        INSERT INTO users (provider, provider_user_id, email)
        VALUES ($1, $2, $3)
        ON CONFLICT (provider, provider_user_id)
        DO UPDATE SET email = COALESCE(EXCLUDED.email, users.email)
        RETURNING id, provider, provider_user_id, email,
                  polar_customer_id, customer_api_key, created_at
        """,
        provider,
        provider_user_id,
        email,
    )
    return _user_from_row(row)


async def get_user(user_id: int) -> User | None:
    if _is_memory_mode():
        return _MEMORY_USERS.get(user_id)
    row = await _pool.fetchrow(
        "SELECT id, provider, provider_user_id, email, polar_customer_id, "
        "customer_api_key, created_at FROM users WHERE id = $1",
        user_id,
    )
    return _user_from_row(row) if row is not None else None


def _user_from_row(row: Any) -> User:
    return User(
        id=row["id"],
        provider=row["provider"],
        provider_user_id=row["provider_user_id"],
        email=row["email"],
        polar_customer_id=row["polar_customer_id"],
        customer_api_key=row["customer_api_key"],
        created_at=row["created_at"],
    )
