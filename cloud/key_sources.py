"""
OpenOSINT Cloud — per-tool key-source configuration.

KeySource controls where /v1/enrich resolves the upstream API key:
  platform         — platform's own env var (sponsored / included perk)
  tenant           — tenant's stored BYOK key (required — 422 if absent)
  tenant_optional  — tenant's stored key when present, else call unauth
  none             — tool needs no credential

To move a tool between platform-provided and tenant-provided, change its
entry here.  No other files need to change.

Canonical provider strings (used by POST /v1/keys and 422 messages):
  "ipinfo"      — ipinfo.io token (search_ip)
  "abuseipdb"   — AbuseIPDB API key (search_abuseipdb)
"""
from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class KeySource(str, Enum):
    platform = "platform"
    tenant = "tenant"
    tenant_optional = "tenant_optional"
    none = "none"


class ToolKeyConfig(NamedTuple):
    env_var: str | None      # platform env-var name; None when source is tenant/none
    source: KeySource
    provider: str | None     # canonical tenant-facing provider string; None for platform/none tools


# Single source of truth for v1 tool credentials.
TOOL_KEY_CONFIG: dict[str, ToolKeyConfig] = {
    # Sponsored — key is platform-provided; tenants get this included.
    "search_ip2location": ToolKeyConfig("IP2LOCATION_API_KEY", KeySource.platform, provider=None),
    # BYOK required — tenant must POST /v1/keys with the provider string below.
    "search_ip":          ToolKeyConfig("IPINFO_TOKEN",        KeySource.tenant,   provider="ipinfo"),
    "search_abuseipdb":   ToolKeyConfig("ABUSEIPDB_API_KEY",   KeySource.tenant,   provider="abuseipdb"),
    # No credential required.
    "search_dns":         ToolKeyConfig(None,                  KeySource.none,     provider=None),
    "search_domain":      ToolKeyConfig(None,                  KeySource.none,     provider=None),
}
