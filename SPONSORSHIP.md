# OpenOSINT — Sponsorship Prospectus

OpenOSINT is an open-source AI-powered OSINT framework used by security researchers, OSINT practitioners, and developers evaluating intelligence APIs.

→ Full media kit with audience and distribution details: [openosint.tech/sponsors.html](https://openosint.tech/sponsors.html)

## Audience

| Metric | Value |
|--------|-------|
| GitHub stars | 607 |
| GitHub forks | 105 |
| PyPI downloads / month | 4,500 |
| Website visits / month | <!-- TODO: from analytics --> |
| MCP Registry | Published — `io.github.OpenOSINT/openosint` |

## Tier

**Featured Integration** — the only sponsorship tier. One vendor per category.

| Billing | Rate |
|---------|------|
| Annual  | $2,000/year (≈ $167/month — 2 months free vs monthly) |
| Monthly | $220/month |

Fiscal host: Open Collective / Open Source Collective — [opencollective.com/openosint_oss](https://opencollective.com/openosint_oss)

**Placements included (all recurring):**

- Recommended/default provider for one tool category (exclusive)
- Logo + name + tagline in README Featured Integrations block
- Sponsor badge in README badge row
- "Featured (sponsored)" label and first listing in the Integrations table
- CLI startup banner on every `openosint` invocation
- Tool documentation — featured with API key sign-up link
- Web UI settings panel — Featured integrations list
- `openosint sponsors` CLI subcommand output
- MCP Registry listing credit
- Listed on [openosint.tech/sponsors.html](https://openosint.tech/sponsors.html)
- Named in release notes

## Open categories

| Category | Status |
|----------|--------|
| Residential & Datacenter Proxy Detection | **OPEN** |
| Breach / Compromised-Credential Data | **OPEN** |
| Threat & Domain Intelligence | **OPEN** |
| Email / Identity Lookup | **OPEN** |
| IP Geolocation & IP Intelligence | TAKEN — IP2Location.io |
| Residential Proxies | TAKEN — RapidProxy |

## Referral funnel

1. User installs OpenOSINT (`pip install openosint` or from source).
2. User reads the Configuration table or tool docs — your API is listed as the recommended provider with a direct sign-up link.
3. User navigates to your pricing or sign-up page.
4. User sets an API key and activates the integration.

## How to add or update a sponsor (maintainer guide)

All sponsor data lives in [`sponsors.json`](sponsors.json). Adding, updating, or removing a sponsor is a **one-file change**.

```json
{
  "name": "Acme Corp",
  "tagline": "Short description of what your product does",
  "url": "https://acme.example.com/?utm_source=openosint",
  "logo": "https://img.shields.io/badge/Acme-sponsored-blue?style=flat-square",
  "tier": "featured",
  "tool": "search_acme",
  "category": "Your Category"
}
```

Then regenerate the README block:

```bash
python scripts/render_sponsors.py
```

CLI banner, Web UI, and `openosint sponsors` update automatically at runtime.

## Current sponsors

**[IP2Location.io](https://www.ip2location.io)** — Featured Integration (IP Geolocation & IP Intelligence)

**[RapidProxy](https://rapidproxy.io/?utm_source=openosint&utm_medium=readme&utm_campaign=featured)** — Featured Integration (Residential Proxies)

## Contact

- Email: [openosint@yahoo.com](mailto:openosint@yahoo.com?subject=OpenOSINT%20Sponsorship%20Inquiry)
- Open Collective: [opencollective.com/openosint_oss](https://opencollective.com/openosint_oss)
- Website: [openosint.tech/sponsors.html](https://openosint.tech/sponsors.html)

---

*OpenOSINT is for authorized security research only. See [DISCLAIMER.md](DISCLAIMER.md).*
