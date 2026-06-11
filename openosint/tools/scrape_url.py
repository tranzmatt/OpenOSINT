# openosint/tools/scrape_url.py
"""
Bright Data Web Unlocker integration.

Fetches any public URL through the Bright Data Web Unlocker API,
bypassing bot-protection (Cloudflare, CAPTCHA, etc.) and returning
clean Markdown via the API's native ``data_format: "markdown"`` conversion.

Requires BRIGHTDATA_API_KEY and BRIGHTDATA_UNLOCKER_ZONE environment variables.

OpenOSINT earns a referral commission if you sign up through our link.
Free tier: 5,000 requests/month — https://get.brightdata.com/984ni58s2oad

# TODO: Future PR — add search_profile_social tool for social-media profile
#       extraction once ToS and rate-limit guidance is confirmed with Bright Data.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

import requests

from openosint.tools.exceptions import OSINTError, ToolExecutionError

logger = logging.getLogger(__name__)

_API_URL = "https://api.brightdata.com/request"
_DEFAULT_TIMEOUT = 60
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

_MISSING_KEY_MSG = (
    "Scan error: BRIGHTDATA_API_KEY environment variable is not set. "
    "A free tier (5,000 requests/month) is available — "
    "sign up at https://get.brightdata.com/984ni58s2oad"
)
_MISSING_ZONE_MSG = (
    "Scan error: BRIGHTDATA_UNLOCKER_ZONE environment variable is not set. "
    "Set it to your Bright Data Web Unlocker zone name (e.g. 'web_unlocker1'). "
    "Create a zone at https://get.brightdata.com/984ni58s2oad"
)

# NOTE: Confirmed against https://docs.brightdata.com/api-reference/rest-api/unlocker/unlock-website.md
# Request: POST https://api.brightdata.com/request
#   { zone, url, format: "json", data_format: "markdown" }
# Response: { status_code: int, headers: {...}, body: "<markdown string>" }
# TODO: verify that 'body' contains markdown (not raw HTML) when data_format="markdown"
#       against a live Bright Data account before shipping to production users.


def _is_valid_url(url: str) -> bool:
    return bool(_URL_RE.match(url.strip()))


def _fetch_unlocker(url: str, api_key: str, zone: str, timeout: int) -> dict:
    try:
        response = requests.post(
            _API_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "zone": zone,
                "url": url,
                "format": "json",
                "data_format": "markdown",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise OSINTError(f"Network error querying Bright Data Web Unlocker: {exc}") from exc

    if response.status_code == 401:
        raise OSINTError("Bright Data Web Unlocker: invalid API key.")
    if response.status_code == 403:
        raise OSINTError("Bright Data Web Unlocker: forbidden — check zone permissions.")
    if response.status_code == 429:
        raise OSINTError("Bright Data Web Unlocker: rate limit exceeded.")
    if response.status_code != 200:
        raise ToolExecutionError(f"Bright Data Web Unlocker returned HTTP {response.status_code}.")

    return response.json()


def _format_unlocker_result(data: dict, url: str) -> str:
    remote_status = data.get("status_code", "unknown")
    body = data.get("body", "")
    header = [
        f"[Web Unlocker] URL: {url}",
        f"[Web Unlocker] Remote status: {remote_status}",
        "",
    ]
    content = str(body) if body else "(empty response body)"
    return "\n".join(header) + content


async def run_scrape_url_osint(
    url: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
) -> str:
    """
    Fetch *url* through Bright Data Web Unlocker and return clean Markdown.

    Bypasses bot-protection (Cloudflare, CAPTCHA, etc.). Uses the API's
    native ``data_format: "markdown"`` conversion so the AI receives
    clean, readable content rather than raw HTML.

    Requires ``BRIGHTDATA_API_KEY`` and ``BRIGHTDATA_UNLOCKER_ZONE`` environment variables.
    OpenOSINT earns a referral commission if you sign up through our link.

    Returns
    -------
    str
        Markdown content with metadata header, or descriptive error message.
    """
    api_key = os.environ.get("BRIGHTDATA_API_KEY", "")
    if not api_key:
        return _MISSING_KEY_MSG

    zone = os.environ.get("BRIGHTDATA_UNLOCKER_ZONE", "")
    if not zone:
        return _MISSING_ZONE_MSG

    url = url.strip()
    if not _is_valid_url(url):
        return "Invalid URL: must start with http:// or https://"

    logger.info("Starting Web Unlocker fetch for: %s", url)
    try:
        data = await asyncio.to_thread(_fetch_unlocker, url, api_key, zone, timeout_seconds)
        result = _format_unlocker_result(data, url)
        logger.info("Web Unlocker fetch complete for: %s", url)
        return result
    except OSINTError as exc:
        logger.warning("Web Unlocker fetch failed: %s", exc)
        return f"Scan error: {exc}"
    except Exception as exc:
        logger.exception("Unexpected error during Web Unlocker fetch.")
        return f"Internal error: {exc}"
