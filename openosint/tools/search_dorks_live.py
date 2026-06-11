# openosint/tools/search_dorks_live.py
"""
Bright Data SERP API integration.

Executes Google dork queries for a target through the Bright Data SERP API,
returning structured results (title, URL, snippet) for each dork.

`generate_dorks` remains fully offline and unchanged; this is a separate,
opt-in tool that requires a Bright Data account.

Requires BRIGHTDATA_API_KEY and BRIGHTDATA_SERP_ZONE environment variables.

OpenOSINT earns a referral commission if you sign up through our link.
Free tier: 5,000 requests/month — https://get.brightdata.com/984ni58s2oad
"""

from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse

import requests

from openosint.tools.exceptions import OSINTError, ToolExecutionError
from openosint.tools.generate_dorks import _DORK_TEMPLATES

logger = logging.getLogger(__name__)

_API_URL = "https://api.brightdata.com/request"
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_DORKS = 5
_GOOGLE_SEARCH_BASE = "https://www.google.com/search?q="

_MISSING_KEY_MSG = (
    "Scan error: BRIGHTDATA_API_KEY environment variable is not set. "
    "A free tier (5,000 requests/month) is available — "
    "sign up at https://get.brightdata.com/984ni58s2oad"
)
_MISSING_ZONE_MSG = (
    "Scan error: BRIGHTDATA_SERP_ZONE environment variable is not set. "
    "Set it to your Bright Data SERP API zone name (e.g. 'serp_api1'). "
    "Create a zone at https://get.brightdata.com/984ni58s2oad"
)

# NOTE: Confirmed against https://docs.brightdata.com/api-reference/rest-api/serp/serp-api.md
# Request: POST https://api.brightdata.com/request
#   { zone, url, format: "json" }  → response has .organic[]{link, title, description}
# TODO: verify 'link' vs 'url' field name against a live response.


def _build_google_url(dork_query: str) -> str:
    return f"{_GOOGLE_SEARCH_BASE}{urllib.parse.quote(dork_query)}&hl=en"


def _fetch_serp(url: str, api_key: str, zone: str, timeout: int) -> dict:
    try:
        response = requests.post(
            _API_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={"zone": zone, "url": url, "format": "json"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise OSINTError(f"Network error querying Bright Data SERP: {exc}") from exc

    if response.status_code == 401:
        raise OSINTError("Bright Data SERP: invalid API key.")
    if response.status_code == 403:
        raise OSINTError("Bright Data SERP: forbidden — check zone permissions.")
    if response.status_code == 429:
        raise OSINTError("Bright Data SERP: rate limit exceeded.")
    if response.status_code != 200:
        raise ToolExecutionError(f"Bright Data SERP returned HTTP {response.status_code}.")

    return response.json()


def _extract_organic(data: dict) -> list[dict]:
    organic = data.get("organic", [])
    results = []
    for item in organic[:5]:
        title = item.get("title", "")
        # SERP API uses 'link'; fallback to 'url' in case field name differs in future versions
        link = item.get("link", "") or item.get("url", "")
        snippet = item.get("description", "") or item.get("snippet", "")
        if link:
            results.append({"title": title, "url": link, "snippet": snippet})
    return results


async def run_dorks_live_osint(
    target: str,
    max_dorks: int = _DEFAULT_MAX_DORKS,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
) -> str:
    """
    Execute Google dork queries for *target* via the Bright Data SERP API.

    Reuses the same dork templates as ``generate_dorks`` but fetches live
    search results instead of generating URLs. Each dork is a separate API
    call — Bright Data bills per successful request.

    Requires ``BRIGHTDATA_API_KEY`` and ``BRIGHTDATA_SERP_ZONE`` environment variables.
    OpenOSINT earns a referral commission if you sign up through our link.

    Returns
    -------
    str
        Formatted results or descriptive error message.
    """
    api_key = os.environ.get("BRIGHTDATA_API_KEY", "")
    if not api_key:
        return _MISSING_KEY_MSG

    zone = os.environ.get("BRIGHTDATA_SERP_ZONE", "")
    if not zone:
        return _MISSING_ZONE_MSG

    target = target.strip()
    if not target:
        return "Invalid input: target must not be empty."

    dorks = _DORK_TEMPLATES[:max_dorks]
    logger.info("Starting live dork search for '%s' (%d dorks)", target, len(dorks))

    lines = [f"Bright Data live dork search for '{target}' ({len(dorks)} queries):\n"]
    error_count = 0

    for template in dorks:
        query = template.format(target=target)
        google_url = _build_google_url(query)
        lines.append(f"[+] Dork: {query}")
        try:
            data = await asyncio.to_thread(_fetch_serp, google_url, api_key, zone, timeout_seconds)
            results = _extract_organic(data)
            if results:
                for r in results:
                    lines.append(f"    Title:   {r['title']}")
                    lines.append(f"    URL:     {r['url']}")
                    if r["snippet"]:
                        lines.append(f"    Snippet: {r['snippet'][:200]}")
                    lines.append("")
            else:
                lines.append("    (no organic results)")
                lines.append("")
        except OSINTError as exc:
            error_count += 1
            logger.warning("SERP dork failed: %s", exc)
            lines.append(f"    (error: {exc})")
            lines.append("")
        except Exception as exc:
            error_count += 1
            logger.exception("Unexpected error during live dork execution.")
            lines.append(f"    (internal error: {exc})")
            lines.append("")

    if error_count == len(dorks):
        return (
            "Scan error: all SERP requests failed. "
            "Check BRIGHTDATA_API_KEY and BRIGHTDATA_SERP_ZONE."
        )

    logger.info("Live dork search complete for: %s", target)
    return "\n".join(lines)
