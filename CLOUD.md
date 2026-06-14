# OpenOSINT Cloud — Deploy & Polar Setup Guide

Hosted REST and MCP API for IP and domain infrastructure intelligence.  One call, one bill, no infra to manage.  Hosted on Heroku, billing via Polar.sh (Merchant of Record — handles international VAT automatically).

**Scope:** IP geolocation, ISP/ASN, proxy/VPN/datacenter/Tor detection, IP abuse reputation, DNS records, subdomain enumeration.  This service does **not** search for personal data about individuals and does not use leaked or breached data sources.

---

## 1. Prerequisites

- Heroku CLI installed and logged in (`heroku login`)
- Polar.sh account at https://polar.sh
- This repo cloned locally

---

## 2. Heroku deploy

```bash
# Create app
heroku create your-app-name

# Add Postgres (free hobby tier is sufficient to start)
heroku addons:create heroku-postgresql:essential-0

# Set required secrets
heroku config:set POLAR_TOKEN=pat_...
heroku config:set POLAR_WEBHOOK_SECRET=whsec_...   # set this after step 3

# Set upstream keys used by the v1 tool set
heroku config:set IP2LOCATION_API_KEY=...
heroku config:set ABUSEIPDB_API_KEY=...
heroku config:set IPINFO_TOKEN=...          # optional, raises ipinfo rate limit

# Deploy
git push heroku main

# Initialise the database schema (run once)
heroku run psql \$DATABASE_URL -f db/init.sql

# Confirm the web dyno is up
heroku open
heroku logs --tail
```

The server binds to `$PORT` (set by Heroku automatically).

---

## 3. Polar setup

### 3a. Create three products

In your Polar dashboard create three products:

| Product  | Type         | Price    | Credits |
|----------|--------------|----------|---------|
| payg     | One-time     | $10      | 100     |
| starter  | Subscription | $19 / mo | 1 000   |
| pro      | Subscription | $49 / mo | 5 000   |

### 3b. Add a License Key benefit to each product

1. Open each product → **Benefits** → **Add benefit** → **License Keys**
2. Name it (e.g. "OpenOSINT Cloud API Key")
3. Save.  Copy the **benefit ID** (`benefit_...`) for each product.

These License Keys are the customers' API keys.  Polar mints them on purchase and displays them in the customer portal — no email flow to build.

### 3c. Set benefit / product IDs as Heroku config vars

```bash
heroku config:set POLAR_BENEFIT_ID_PAYG=benefit_...
heroku config:set POLAR_BENEFIT_ID_STARTER=benefit_...
heroku config:set POLAR_BENEFIT_ID_PRO=benefit_...

heroku config:set POLAR_PRODUCT_ID_STARTER=prod_...
heroku config:set POLAR_PRODUCT_ID_PRO=prod_...
```

### 3d. Set checkout URLs

Copy the hosted checkout URL for each product from Polar and set:

```bash
heroku config:set POLAR_CHECKOUT_PAYG=https://polar.sh/...
heroku config:set POLAR_CHECKOUT_STARTER=https://polar.sh/...
heroku config:set POLAR_CHECKOUT_PRO=https://polar.sh/...
```

### 3e. Register the webhook endpoint

1. Polar dashboard → **Developer** → **Webhooks** → **Add endpoint**
2. URL: `https://your-app-name.herokuapp.com/v1/polar/webhook`
3. Subscribe to these events:
   - `benefit_grant.created`
   - `benefit_grant.updated`
   - `benefit_grant.revoked`
   - `subscription.updated`
4. Copy the **Signing Secret** (`whsec_...`) and set it:
   ```bash
   heroku config:set POLAR_WEBHOOK_SECRET=whsec_...
   ```
5. Send a **test event** for each type and verify the response is `{"status":"ok"}`.
   Use `heroku logs --tail` to see the handler output.

> ⚠️  **Verify event field paths against the live test-event payload.**
> The webhook handler reads the license key from
> `data.properties.license_key.key` with a `data.properties.display_key`
> fallback.  If neither field exists in the real payload, the handler logs an
> error and skips the upsert.  Check `heroku logs` after the first real purchase.

---

## 4. Running locally

```bash
# Install with cloud extras
pip install -e ".[dev]"
pip install fastapi uvicorn[standard] asyncpg httpx pydantic

# Copy and fill in secrets (DATABASE_URL optional — omit for in-memory backend)
cp .env.example .env

# Start the gateway
uvicorn cloud.main:app --reload --port 8000
```

---

## 5. Running the test suite

```bash
pytest tests/test_cloud.py -v
```

No network calls are made.  The tests run against the in-memory backend.

---

## 6. MCP server endpoint

The gateway exposes a hosted MCP server at `/mcp` using the Streamable HTTP transport (MCP SDK ≥ 1.0.0).  Connect any MCP-compatible client — Claude Desktop, Claude Code, or a custom agent — directly to the hosted API without running any local server.

### Connection URL

```
https://your-app.herokuapp.com/mcp
```

### Authentication

Pass your OpenOSINT Cloud license key as an **`Authorization: Bearer`** header.  In `claude_desktop_config.json` or equivalent:

```json
{
  "mcpServers": {
    "openosint-cloud": {
      "url": "https://your-app.herokuapp.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_LICENSE_KEY"
      }
    }
  }
}
```

### Available MCP tools (5)

The same 5 infrastructure tools as `/v1/enrich`:

| MCP tool | What it returns | Key source |
|---|---|---|
| `search_ip` | Geolocation, ISP, ASN, hostname (ipinfo.io) | customer BYOK — `ipinfo` |
| `search_ip2location` | Proxy/VPN/Tor/datacenter, threat score (IP2Location.io) | server — included |
| `search_abuseipdb` | Abuse confidence score, reports, ISP (AbuseIPDB) | customer BYOK — `abuseipdb` |
| `search_dns` | A, AAAA, MX, NS, TXT, CNAME, SOA records | none |
| `search_domain` | Subdomain enumeration (passive DNS) | none |

Each tool takes one string parameter `target` (an IP address or domain name).  BYOK tools require the corresponding key to be stored first via `POST /v1/keys`; a missing key returns a structured error string, not a protocol error.

### Credit metering

1 credit is deducted on success — same rule as the REST endpoint.  Upstream errors (`Scan error:...`) are **not** charged.

### Listing on Smithery / Glama / mcp.so

Point the registry at `https://your-app.herokuapp.com/mcp`.  The server's capability advertisement (tool list, descriptions) is returned via the standard `initialize` → `tools/list` MCP exchange; no separate manifest URL is needed.

---

## 7. Syntax / import check

```bash
python -m py_compile cloud/main.py cloud/db.py cloud/polar.py cloud/tools.py \
  cloud/auth.py cloud/config.py \
  cloud/routes/enrich.py cloud/routes/usage.py \
  cloud/routes/checkout.py cloud/routes/webhook.py
```

---

## 8. curl examples

### Check your balance

```bash
curl -s https://your-app.herokuapp.com/v1/usage \
  -H "X-API-Key: YOUR_LICENSE_KEY" | jq .
# → {"plan":"starter","credits":999}
```

### Run an OSINT tool

```bash
curl -s -X POST https://your-app.herokuapp.com/v1/enrich \
  -H "X-API-Key: YOUR_LICENSE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool":"search_ip","target":"8.8.8.8"}' | jq .
```

```json
{
  "tool": "search_ip",
  "target": "8.8.8.8",
  "timestamp": "2026-06-12T10:00:00+00:00",
  "results": [
    "IP intelligence for '8.8.8.8':",
    "[+] Ip: 8.8.8.8",
    "[+] Hostname: dns.google",
    "[+] Org: AS15169 Google LLC",
    "[+] City: Mountain View",
    "[+] Country: US"
  ],
  "error": null,
  "credits_left": 998
}
```

### Get a checkout URL (for 402 → top-up flow)

```bash
curl -s "https://your-app.herokuapp.com/v1/checkout?plan=starter" | jq .
# → {"plan":"starter","credits":1000,"url":"https://polar.sh/..."}
```

---

## 9. Polar event names wired (verify before going live)

| Event string          | Handler action                                 |
|-----------------------|------------------------------------------------|
| `benefit_grant.created` | Extract license key, upsert customer + credits |
| `benefit_grant.updated` | Same — handles key refresh                    |
| `benefit_grant.revoked` | Zero credits                                  |
| `subscription.updated`  | If status == "active": refill credits to plan amount |

> Use Polar's **Send test event** feature to confirm these string values match
> what Polar actually sends before processing real purchases.

---

## 10. Environment variable reference

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Production | Heroku Postgres DSN (set by addon) |
| `POLAR_TOKEN` | Production | Polar API access token |
| `POLAR_WEBHOOK_SECRET` | Production | Webhook HMAC signing secret |
| `POLAR_CHECKOUT_*` | Production | Hosted checkout URLs per plan |
| `POLAR_BENEFIT_ID_*` | Production | Benefit IDs for plan mapping |
| `POLAR_PRODUCT_ID_*` | Production | Product IDs for renewal refill |
| `IP2LOCATION_API_KEY` | Recommended | search_ip2location tool (sponsored) |
| `ABUSEIPDB_API_KEY` | Recommended | search_abuseipdb tool |
| `IPINFO_TOKEN` | Optional | Raises ipinfo.io rate limit |

---

## 11. v1 synchronous tool allow-list

IP and domain infrastructure intelligence only.

| Tool | What it returns | Upstream | Key source | Provider string | Typical latency |
|---|---|---|---|---|---|
| `search_ip` | Geolocation, ISP, ASN, hostname | ipinfo.io | **customer** (BYOK) | `ipinfo` | ~1 s |
| `search_ip2location` | Proxy/VPN/Tor/datacenter detection, threat score | IP2Location.io (sponsored) | server | — | ~1–2 s |
| `search_abuseipdb` | IP abuse reputation, report history | AbuseIPDB | **customer** (BYOK) | `abuseipdb` | ~1–2 s |
| `search_dns` | A, AAAA, MX, NS, TXT, CNAME, SOA records | dnspython | none | — | ~2–5 s |
| `search_domain` | Subdomain enumeration (passive DNS) | sublist3r | none | — | ~10–30 s |

**Key source legend:**
- **customer** — customer must add their own key via `POST /v1/keys` before calling this tool; missing key returns 422.
- **server** — key is provided by the operator; customers get it included at no extra step.
- **none** — no credential required.

Credits are **not** deducted when a tool returns an upstream error (`Scan error: ...`).

All tool calls are wrapped in a 25 s `asyncio.wait_for` (Heroku 30 s H12 limit - 5 s headroom).  A 504 is returned if the tool exceeds the budget.

---

## 12. BYOK key management

Customers store their own upstream API keys once; every `POST /v1/enrich` call resolves them automatically.  Keys are encrypted at rest with Fernet (`CONFIG_ENCRYPTION_KEY`).

### Add a key

```bash
# Add an ipinfo.io token
curl -s -X POST https://your-app.herokuapp.com/v1/keys \
  -H "X-API-Key: YOUR_LICENSE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "ipinfo", "secret": "your_ipinfo_token"}'
# -> HTTP 204 No Content

# Add an AbuseIPDB key
curl -s -X POST https://your-app.herokuapp.com/v1/keys \
  -H "X-API-Key: YOUR_LICENSE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "abuseipdb", "secret": "your_abuseipdb_key"}'

```

### List stored keys (masked)

```bash
curl -s https://your-app.herokuapp.com/v1/keys \
  -H "X-API-Key: YOUR_LICENSE_KEY" | jq .
# -> [{"provider":"ipinfo","masked":"****a1b2"},{"provider":"abuseipdb","masked":"****xy99"}]
```

### Remove a key

```bash
curl -s -X DELETE https://your-app.herokuapp.com/v1/keys/ipinfo \
  -H "X-API-Key: YOUR_LICENSE_KEY"
# -> HTTP 204 No Content
```

### End-to-end: store an ipinfo key then run search_ip

```bash
# 1. Store the key (once)
curl -s -X POST https://your-app.herokuapp.com/v1/keys \
  -H "X-API-Key: YOUR_LICENSE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "ipinfo", "secret": "your_ipinfo_token"}'

# 2. Run search_ip -- stored key is resolved automatically, no extra header needed
curl -s -X POST https://your-app.herokuapp.com/v1/enrich \
  -H "X-API-Key: YOUR_LICENSE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tool": "search_ip", "target": "8.8.8.8"}' | jq .
```

### Operator setup: CONFIG_ENCRYPTION_KEY

```bash
# Generate a Fernet key (do this once, store it safely)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Set it on Heroku before first deployment with DATABASE_URL
heroku config:set CONFIG_ENCRYPTION_KEY=<generated_key>
```

> **Warning:** Never rotate `CONFIG_ENCRYPTION_KEY` without first decrypting and
> re-encrypting all rows in `customer_keys`.  Rotating without migration makes all
> stored secrets unreadable.
