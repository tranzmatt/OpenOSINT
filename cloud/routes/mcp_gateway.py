"""
OpenOSINT Cloud — hosted MCP server endpoint (Streamable HTTP transport).

Mounted at /mcp inside the existing FastAPI app (cloud/main.py).
Exposes exactly the same 5 infrastructure tools as /v1/enrich.
No person-search, breach, or leaked-data tools.

Auth: Authorization: Bearer <openosint-cloud-api-key>
Metering: 1 credit per successful call — same rules as /v1/enrich.
All existing functions (dispatch, _resolve_key, decrement_credits) are reused.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp, Receive, Scope, Send

from cloud import db, keys, tools
from cloud.config import CHECKOUT_URLS, TOOL_TIMEOUT_SECONDS
from cloud.key_sources import TOOL_KEY_CONFIG, KeySource

logger = logging.getLogger(__name__)

# ── per-request customer, set by _AuthMiddleware before the MCP handler runs ─
_customer_ctx: ContextVar[db.Customer | None] = ContextVar("_mcp_customer", default=None)

_ERROR_PREFIXES = ("Scan error", "Internal error", "Error:")

# ── FastMCP instance ──────────────────────────────────────────────────────────
# streamable_http_path="/" because FastAPI mounts this at /mcp and Starlette
# strips the /mcp prefix before passing the request to the sub-app.
_mcp = FastMCP(
    "OpenOSINT Cloud",
    streamable_http_path="/",
)


# ── shared business logic (mirrors cloud/routes/enrich.py) ───────────────────

async def _resolve_key(tool: str, customer: db.Customer) -> str | None:
    """Resolve upstream API key from TOOL_KEY_CONFIG — identical to /v1/enrich."""
    cfg = TOOL_KEY_CONFIG.get(tool)
    if cfg is None or cfg.source == KeySource.none:
        return None
    if cfg.source == KeySource.server:
        return os.environ.get(cfg.env_var or "", "") or None
    stored = await keys.get_key(customer.api_key, cfg.provider)
    if cfg.source == KeySource.customer and stored is None:
        raise ValueError(
            f"Tool '{tool}' requires a '{cfg.provider}' API key. "
            f"Add it via: POST /v1/keys "
            f'{{"provider": "{cfg.provider}", "secret": "your_key"}}'
        )
    return stored


def _credits_error(plan: str) -> str:
    checkout_url = CHECKOUT_URLS.get(plan) or CHECKOUT_URLS.get("payg", "")
    suffix = f" Top up at: {checkout_url}" if checkout_url else ""
    return f"No credits remaining.{suffix}"


async def _run_mcp_tool(tool_name: str, target: str) -> str:
    """
    Common MCP tool handler: validate input → auth → key resolution →
    dispatch → meter. Returns a plain-text result or a structured error string.
    Never raises — MCP tool handlers must not throw to avoid protocol 500s.
    """
    if not target or not target.strip():
        return (
            f"Error: 'target' is required for {tool_name}. "
            "Provide an IP address or domain name."
        )

    customer = _customer_ctx.get()
    if customer is None:
        return (
            "Error: Invalid or missing API key. "
            "Pass your OpenOSINT Cloud key as: Authorization: Bearer <key>"
        )

    try:
        upstream_key = await _resolve_key(tool_name, customer)
    except ValueError as exc:
        return f"Error: {exc}"

    if customer.credits <= 0:
        return _credits_error(customer.plan)

    try:
        result = await asyncio.wait_for(
            tools.dispatch(tool_name, target.strip(), api_key=upstream_key),
            timeout=float(TOOL_TIMEOUT_SECONDS),
        )
    except asyncio.TimeoutError:
        return f"Error: Tool '{tool_name}' timed out after {TOOL_TIMEOUT_SECONDS}s."
    except ValueError as exc:
        return f"Error: {exc}"

    lines: list[str] = result.get("results", [])
    first_line = lines[0] if lines else (result.get("error") or "")
    is_error = any(first_line.startswith(p) for p in _ERROR_PREFIXES)

    if not is_error:
        new_credits = await db.decrement_credits(customer.api_key)
        if new_credits is None:
            # Race condition: concurrent request drained the last credit
            return _credits_error(customer.plan)

    return "\n".join(lines) if lines else (result.get("error") or "No results.")


# ── MCP tool registrations (5 infrastructure tools, same as ALLOW_LIST) ──────

@_mcp.tool(description=(
    "Retrieve geolocation, ASN, and host data for an IP address via ipinfo.io. "
    "Returns country, city, organisation, hostname, and timezone."
))
async def search_ip(target: str) -> str:
    """target: IPv4 or IPv6 address (e.g. 8.8.8.8)"""
    return await _run_mcp_tool("search_ip", target)


@_mcp.tool(description=(
    "Enhanced IP intelligence: geolocation, ISP, VPN/Proxy/Tor/datacenter detection, "
    "threat score. Sponsored by IP2Location.io — server key included, no setup required."
))
async def search_ip2location(target: str) -> str:
    """target: IPv4 or IPv6 address (e.g. 8.8.8.8)"""
    return await _run_mcp_tool("search_ip2location", target)


@_mcp.tool(description=(
    "Check an IP address against AbuseIPDB for malicious activity reports, "
    "abuse confidence score, total reports, and ISP."
))
async def search_abuseipdb(target: str) -> str:
    """target: IPv4 or IPv6 address (e.g. 8.8.8.8)"""
    return await _run_mcp_tool("search_abuseipdb", target)


@_mcp.tool(description=(
    "Enumerate DNS records for a domain: A, AAAA, MX, NS, TXT, CNAME, SOA."
))
async def search_dns(target: str) -> str:
    """target: Fully qualified domain name (e.g. example.com)"""
    return await _run_mcp_tool("search_dns", target)


@_mcp.tool(description=(
    "Enumerate subdomains for a target domain via passive DNS intelligence sources."
))
async def search_domain(target: str) -> str:
    """target: Apex domain name (e.g. example.com)"""
    return await _run_mcp_tool("search_domain", target)


# ── auth middleware ───────────────────────────────────────────────────────────

class _AuthMiddleware:
    """
    Raw ASGI middleware: extract Authorization: Bearer <key>, look up the
    customer, and store it in _customer_ctx before the MCP handler runs.

    Uses a raw ASGI wrapper (not BaseHTTPMiddleware) to guarantee that
    ContextVar values set here are visible inside the tool handlers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers: dict[bytes, bytes] = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode()
            api_key = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            customer = await db.get_customer(api_key) if api_key else None
            token = _customer_ctx.set(customer)
            try:
                await self._app(scope, receive, send)
            finally:
                _customer_ctx.reset(token)
        else:
            await self._app(scope, receive, send)


# ── ASGI app factory ──────────────────────────────────────────────────────────

def create_mcp_asgi_app() -> ASGIApp:
    """Return the FastMCP Starlette app wrapped with the auth middleware."""
    starlette_app = _mcp.streamable_http_app()
    return _AuthMiddleware(starlette_app)
