"""
Smoke tests for OpenOSINT Cloud gateway.

Runs against the in-memory DB backend (DATABASE_URL not set).
Tool dispatch is mocked — no real network calls.

Coverage:
  (a) 401 on missing / invalid API key
  (b) 402 when credits are exhausted
  (c) success path decrements credits and returns structured result
  (d) non-allow-listed tool returns 400
  (e) key store/retrieve roundtrip — encrypted at rest, masked on GET /v1/keys
  (f) enrich uses stored customer key and passes it to the tool
  (g) missing customer key returns 422 without decrementing credits
  (h) server-source tool (ip2location) reads key from env, not customer store
  (i) upstream error leaves credits unchanged
  (j) allow-list is exactly the 5 infrastructure tools; removed tools return 400
  (k) webhook stores full license key (not display_key)
"""
from __future__ import annotations

import dataclasses
import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from cloud import db, keys
from cloud.main import create_app
from cloud.routes.webhook import _handle_benefit_grant, _handle_checkout_updated

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_memory_store():
    """Clear in-memory state before each test to prevent cross-test pollution."""
    db._MEMORY_CUSTOMERS.clear()
    db._MEMORY_BY_POLAR_ID.clear()
    db._MEMORY_EVENTS.clear()
    db._MEMORY_USERS.clear()
    db._MEMORY_USERS_BY_IDENTITY.clear()
    db._next_user_id = 1
    keys._MEMORY_KEYS.clear()
    # Reset cached Fernet so tests always get a fresh ephemeral key
    keys._fernet = None
    yield


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _seed(api_key: str, credits: int = 10, plan: str = "starter") -> db.Customer:
    """Insert a test customer into the in-memory store."""
    customer = db.Customer(
        api_key=api_key,
        polar_customer_id="polar_test_cust",
        credits=credits,
        plan=plan,
    )
    db._MEMORY_CUSTOMERS[api_key] = customer
    db._MEMORY_BY_POLAR_ID["polar_test_cust"] = api_key
    return customer


# ── (a) authentication ────────────────────────────────────────────────────────


async def test_missing_api_key_returns_401(client):
    resp = await client.post("/v1/enrich", json={"tool": "search_ip", "target": "8.8.8.8"})
    assert resp.status_code == 401


async def test_invalid_api_key_returns_401(client):
    resp = await client.post(
        "/v1/enrich",
        json={"tool": "search_ip", "target": "8.8.8.8"},
        headers={"X-API-Key": "not-a-real-key"},
    )
    assert resp.status_code == 401


# ── (b) credits exhausted ─────────────────────────────────────────────────────


async def test_zero_credits_returns_402(client):
    _seed("key-402", credits=0)
    # search_dns has KeySource.none — key resolution is skipped, so 402 fires cleanly
    resp = await client.post(
        "/v1/enrich",
        json={"tool": "search_dns", "target": "example.com"},
        headers={"X-API-Key": "key-402"},
    )
    assert resp.status_code == 402
    body = resp.json()
    assert "checkout_url" in body["detail"]


# ── (c) success path ──────────────────────────────────────────────────────────


async def test_success_decrements_credits_and_returns_result(client):
    _seed("key-200", credits=5)
    fake_result = {
        "tool": "search_dns",
        "target": "example.com",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "results": ["[+] A: 93.184.216.34"],
        "error": None,
    }
    with patch("cloud.tools.dispatch", new=AsyncMock(return_value=fake_result)):
        resp = await client.post(
            "/v1/enrich",
            json={"tool": "search_dns", "target": "example.com"},
            headers={"X-API-Key": "key-200"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["credits_left"] == 4
    assert body["results"] == ["[+] A: 93.184.216.34"]
    assert body["error"] is None
    # Confirm the DB was actually mutated
    assert db._MEMORY_CUSTOMERS["key-200"].credits == 4


# ── (d) tool not on allow-list ────────────────────────────────────────────────


async def test_non_allowlisted_tool_returns_400(client):
    _seed("key-400", credits=10)
    resp = await client.post(
        "/v1/enrich",
        json={"tool": "search_breach", "target": "8.8.8.8"},
        headers={"X-API-Key": "key-400"},
    )
    assert resp.status_code == 400
    # Credits must NOT have been decremented for a rejected tool
    assert db._MEMORY_CUSTOMERS["key-400"].credits == 10


# ── usage endpoint ────────────────────────────────────────────────────────────


async def test_usage_returns_credits_and_plan(client):
    _seed("key-usage", credits=7, plan="pro")
    resp = await client.get("/v1/usage", headers={"X-API-Key": "key-usage"})
    assert resp.status_code == 200
    assert resp.json() == {"plan": "pro", "credits": 7}


# ── (e) key store / retrieve roundtrip ───────────────────────────────────────


async def test_key_store_retrieve_roundtrip():
    _seed("key-roundtrip")
    await keys.store_key("key-roundtrip", "ipinfo", "tok_abc123")

    # Plaintext is recoverable
    assert await keys.get_key("key-roundtrip", "ipinfo") == "tok_abc123"

    # Raw store holds ciphertext, not plaintext
    raw = keys._MEMORY_KEYS[("key-roundtrip", "ipinfo")]
    assert b"tok_abc123" not in raw

    # Masked value shows exactly last 4 chars
    listed = await keys.list_keys("key-roundtrip")
    assert listed == [{"provider": "ipinfo", "masked": "****c123"}]

    # Short secret (≤4 chars) never leaks
    assert keys.mask("ab") == "****"
    assert keys.mask("abcd") == "****"
    assert keys.mask("abcde") == "****bcde"


# ── (e2) censys compound secret: bad format rejected, good format round-trips ─


async def test_censys_secret_bad_format_returns_422(client):
    _seed("key-censys-bad", credits=5)
    resp = await client.post(
        "/v1/keys",
        json={"provider": "censys", "secret": "no-colon-here"},
        headers={"X-API-Key": "key-censys-bad"},
    )
    assert resp.status_code == 422
    assert "censys" in resp.json()["detail"]
    assert await keys.get_key("key-censys-bad", "censys") is None


async def test_censys_secret_well_formed_round_trips_to_censys_keys(client):
    from cloud.tools import _censys_keys

    _seed("key-censys-good", credits=5)
    resp = await client.post(
        "/v1/keys",
        json={"provider": "censys", "secret": "myapiid:myapisecret"},
        headers={"X-API-Key": "key-censys-good"},
    )
    assert resp.status_code == 204

    stored = await keys.get_key("key-censys-good", "censys")
    assert stored == "myapiid:myapisecret"
    assert _censys_keys(stored) == {
        "CENSYS_API_ID": "myapiid",
        "CENSYS_SECRET": "myapisecret",
    }


# ── (f) enrich uses stored customer key ──────────────────────────────────────


async def test_enrich_uses_stored_customer_key(client):
    _seed("key-byok", credits=5)
    await keys.store_key("key-byok", "ipinfo", "tok_abc123")

    captured: list[str | None] = []

    async def fake_dispatch(tool, target, api_key=None):
        captured.append(api_key)
        return {
            "tool": tool,
            "target": target,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "results": ["[+] IP: 1.2.3.4"],
            "error": None,
        }

    with patch("cloud.tools.dispatch", new=fake_dispatch):
        resp = await client.post(
            "/v1/enrich",
            json={"tool": "search_ip", "target": "1.2.3.4"},
            headers={"X-API-Key": "key-byok"},
        )

    assert resp.status_code == 200
    assert captured == ["tok_abc123"]
    assert db._MEMORY_CUSTOMERS["key-byok"].credits == 4


# ── (g) missing customer key → 422, credits unchanged ────────────────────────


async def test_missing_customer_key_returns_422_no_credit_deduct(client):
    _seed("key-nokey", credits=5)
    # No ipinfo key stored

    resp = await client.post(
        "/v1/enrich",
        json={"tool": "search_ip", "target": "1.2.3.4"},
        headers={"X-API-Key": "key-nokey"},
    )

    assert resp.status_code == 422
    assert "ipinfo" in resp.json()["detail"]
    # Credits must be untouched
    assert db._MEMORY_CUSTOMERS["key-nokey"].credits == 5


# ── (h) server-source tool reads key from env ────────────────────────────────


async def test_server_source_reads_env_key(client):
    _seed("key-server", credits=5)
    captured: list[str | None] = []

    async def fake_dispatch(tool, target, api_key=None):
        captured.append(api_key)
        return {
            "tool": tool,
            "target": target,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "results": ["[IP2Location] IP: 1.2.3.4"],
            "error": None,
        }

    with patch("cloud.tools.dispatch", new=fake_dispatch):
        with patch.dict(os.environ, {"IP2LOCATION_API_KEY": "srv_key_xyz"}):
            resp = await client.post(
                "/v1/enrich",
                json={"tool": "search_ip2location", "target": "1.2.3.4"},
                headers={"X-API-Key": "key-server"},
            )

    assert resp.status_code == 200
    assert captured == ["srv_key_xyz"]
    assert db._MEMORY_CUSTOMERS["key-server"].credits == 4


# ── (h2) per-tool credit cost + platform-pool burst limiter ─────────────────


async def test_shodan_costs_configured_credit_amount(client):
    from cloud.config import SHODAN_CREDIT_COST

    _seed("key-shodan-cost", credits=10)

    async def fake_dispatch(tool, target, api_key=None):
        return {
            "tool": tool,
            "target": target,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "results": ["[Shodan] Host: 1.2.3.4"],
            "error": None,
        }

    with patch("cloud.tools.dispatch", new=fake_dispatch):
        with patch.dict(os.environ, {"SHODAN_API_KEY": "srv_shodan_key"}):
            resp = await client.post(
                "/v1/enrich",
                json={"tool": "search_shodan", "target": "1.2.3.4"},
                headers={"X-API-Key": "key-shodan-cost"},
            )

    assert resp.status_code == 200
    assert resp.json()["credits_left"] == 10 - SHODAN_CREDIT_COST
    assert db._MEMORY_CUSTOMERS["key-shodan-cost"].credits == 10 - SHODAN_CREDIT_COST


async def test_platform_pool_burst_limit_returns_429(client):
    from cloud.config import SHODAN_CREDIT_COST
    from cloud.rate_limit import InProcessSlidingWindowLimiter

    _seed("key-burst", credits=10)

    async def fake_dispatch(tool, target, api_key=None):
        return {
            "tool": tool,
            "target": target,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "results": ["[Shodan] Host: 1.2.3.4"],
            "error": None,
        }

    one_call_limiter = InProcessSlidingWindowLimiter(window_secs=60, max_calls=1)
    with patch("cloud.rate_limit.platform_pool_limiter", one_call_limiter):
        with patch("cloud.tools.dispatch", new=fake_dispatch):
            with patch.dict(os.environ, {"SHODAN_API_KEY": "srv_shodan_key"}):
                first = await client.post(
                    "/v1/enrich",
                    json={"tool": "search_shodan", "target": "1.2.3.4"},
                    headers={"X-API-Key": "key-burst"},
                )
                second = await client.post(
                    "/v1/enrich",
                    json={"tool": "search_shodan", "target": "1.2.3.4"},
                    headers={"X-API-Key": "key-burst"},
                )

    assert first.status_code == 200
    assert second.status_code == 429
    # Rejected (429) call must not be charged
    assert db._MEMORY_CUSTOMERS["key-burst"].credits == 10 - SHODAN_CREDIT_COST


# ── (i) upstream error leaves credits unchanged ──────────────────────────────


async def test_upstream_error_leaves_credits_unchanged(client):
    _seed("key-err", credits=5)
    await keys.store_key("key-err", "ipinfo", "tok_abc123")

    error_result = {
        "tool": "search_ip",
        "target": "1.2.3.4",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "results": ["Scan error: network failure"],
        "error": None,
    }

    with patch("cloud.tools.dispatch", new=AsyncMock(return_value=error_result)):
        resp = await client.post(
            "/v1/enrich",
            json={"tool": "search_ip", "target": "1.2.3.4"},
            headers={"X-API-Key": "key-err"},
        )

    assert resp.status_code == 200
    assert resp.json()["results"] == ["Scan error: network failure"]
    assert resp.json()["credits_left"] == 5
    assert db._MEMORY_CUSTOMERS["key-err"].credits == 5


async def test_platform_pool_upstream_error_charges_zero_credits(client):
    """A >1-cost platform tool (Shodan) must not charge on upstream failure."""
    _seed("key-shodan-err", credits=10)

    error_result = {
        "tool": "search_shodan",
        "target": "1.2.3.4",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "results": ["Scan error: Shodan quota exceeded"],
        "error": None,
    }

    with patch("cloud.tools.dispatch", new=AsyncMock(return_value=error_result)):
        with patch.dict(os.environ, {"SHODAN_API_KEY": "srv_shodan_key"}):
            resp = await client.post(
                "/v1/enrich",
                json={"tool": "search_shodan", "target": "1.2.3.4"},
                headers={"X-API-Key": "key-shodan-err"},
            )

    assert resp.status_code == 200
    assert resp.json()["results"] == ["Scan error: Shodan quota exceeded"]
    assert resp.json()["credits_left"] == 10
    assert db._MEMORY_CUSTOMERS["key-shodan-err"].credits == 10


# ── (j) allow-list shape and removed-tool 400s ───────────────────────────────

from cloud.tools import ALLOW_LIST as _ALLOW_LIST

_EXPECTED_TOOLS = {
    "search_ip", "search_ip2location", "search_abuseipdb", "search_dns", "search_domain",
    "search_shodan", "search_virustotal", "search_censys",
}


def test_allow_list_is_exactly_the_infrastructure_tools():
    assert set(_ALLOW_LIST.keys()) == _EXPECTED_TOOLS


async def test_search_github_returns_400(client):
    _seed("key-gh-400", credits=5)
    resp = await client.post(
        "/v1/enrich",
        json={"tool": "search_github", "target": "octocat"},
        headers={"X-API-Key": "key-gh-400"},
    )
    assert resp.status_code == 400
    assert db._MEMORY_CUSTOMERS["key-gh-400"].credits == 5


async def test_search_paste_returns_400(client):
    _seed("key-paste-400", credits=5)
    resp = await client.post(
        "/v1/enrich",
        json={"tool": "search_paste", "target": "example@example.com"},
        headers={"X-API-Key": "key-paste-400"},
    )
    assert resp.status_code == 400
    assert db._MEMORY_CUSTOMERS["key-paste-400"].credits == 5


async def test_generate_dorks_returns_400(client):
    _seed("key-dorks-400", credits=5)
    resp = await client.post(
        "/v1/enrich",
        json={"tool": "generate_dorks", "target": "example.com"},
        headers={"X-API-Key": "key-dorks-400"},
    )
    assert resp.status_code == 400
    assert db._MEMORY_CUSTOMERS["key-dorks-400"].credits == 5


# ── (k) webhook stores full license key (not display_key) ────────────────────


async def test_benefit_grant_created_fetches_full_license_key():
    grant_data = {
        "customer_id": "polar_cust_001",
        "benefit_id": "benefit_payg",
        "properties": {
            "license_key_id": "lk_abc123",
            "display_key": "OPEN****KEY",
        },
    }

    with patch("cloud.polar.fetch_license_key", new=AsyncMock(return_value="FULLKEY")):
        await _handle_benefit_grant(grant_data)

    # Full key stored — display_key never used
    assert "FULLKEY" in db._MEMORY_CUSTOMERS
    assert db._MEMORY_CUSTOMERS["FULLKEY"].api_key == "FULLKEY"
    assert db._MEMORY_CUSTOMERS["FULLKEY"].polar_customer_id == "polar_cust_001"


# ── (l) Shodan attribution appended only for search_shodan ───────────────────


async def test_dispatch_appends_shodan_attribution():
    from cloud import tools as cloud_tools

    with patch("cloud.tools.run_shodan_osint", new=AsyncMock(return_value="[Shodan] Host: 1.2.3.4")):
        result = await cloud_tools.dispatch("search_shodan", "1.2.3.4", api_key="k")

    assert result["results"][-1] == "Data provided by Shodan (shodan.io)."


async def test_dispatch_does_not_attribute_other_tools():
    from cloud import tools as cloud_tools

    with patch("cloud.tools.run_dns_osint", new=AsyncMock(return_value="A: 1.2.3.4")):
        result = await cloud_tools.dispatch("search_dns", "example.com", api_key=None)

    assert "Data provided by Shodan (shodan.io)." not in result["results"]


async def test_shodan_attribution_reaches_rest_response_body(client):
    """Attribution must survive to the actual JSON body the client parses,
    not just the internal dispatch() dict — real dispatch() runs here, only
    the low-level upstream call is mocked."""
    _seed("key-shodan-attr", credits=10)

    with patch("cloud.tools.run_shodan_osint", new=AsyncMock(return_value="[Shodan] Host: 1.2.3.4")):
        with patch.dict(os.environ, {"SHODAN_API_KEY": "srv_shodan_key"}):
            resp = await client.post(
                "/v1/enrich",
                json={"tool": "search_shodan", "target": "1.2.3.4"},
                headers={"X-API-Key": "key-shodan-attr"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == ["[Shodan] Host: 1.2.3.4", "Data provided by Shodan (shodan.io)."]


# ── (n) checkout.updated <-> benefit_grant order-independent rendezvous ─────


async def test_checkout_before_benefit_grant_completes_link_on_benefit_grant():
    user = await db.get_or_create_user("github", "gh_1", "a@example.com")

    await _handle_checkout_updated({
        "status": "succeeded",
        "customer_id": "cust_order_a",
        "metadata": {"reference_id": str(user.id)},
    })

    linked = await db.get_user(user.id)
    assert linked.polar_customer_id == "cust_order_a"
    assert linked.customer_api_key is None  # no customers row yet

    with patch("cloud.polar.fetch_license_key", new=AsyncMock(return_value="FULLKEY_A")):
        await _handle_benefit_grant({
            "customer_id": "cust_order_a",
            "benefit_id": "benefit_payg",
            "properties": {"license_key_id": "lk_a"},
        })

    completed = await db.get_user(user.id)
    assert completed.customer_api_key == "FULLKEY_A"


async def test_benefit_grant_before_checkout_completes_link_on_checkout():
    user = await db.get_or_create_user("github", "gh_2", "b@example.com")

    with patch("cloud.polar.fetch_license_key", new=AsyncMock(return_value="FULLKEY_B")):
        await _handle_benefit_grant({
            "customer_id": "cust_order_b",
            "benefit_id": "benefit_payg",
            "properties": {"license_key_id": "lk_b"},
        })

    # Customer created; user not linked yet since checkout.updated hasn't landed
    assert db._MEMORY_CUSTOMERS["FULLKEY_B"].polar_customer_id == "cust_order_b"
    pre_link = await db.get_user(user.id)
    assert pre_link.polar_customer_id is None

    await _handle_checkout_updated({
        "status": "succeeded",
        "customer_id": "cust_order_b",
        "metadata": {"reference_id": str(user.id)},
    })

    linked = await db.get_user(user.id)
    assert linked.polar_customer_id == "cust_order_b"
    assert linked.customer_api_key == "FULLKEY_B"


async def test_checkout_updated_missing_or_nonnumeric_reference_id_skips_link_silently():
    # No metadata / reference_id at all — must not crash.
    await _handle_checkout_updated({"status": "succeeded", "customer_id": "cust_order_c"})
    assert db._MEMORY_USERS == {}

    # Non-numeric reference_id (shouldn't happen, but must not crash either).
    await _handle_checkout_updated({
        "status": "succeeded",
        "customer_id": "cust_order_c",
        "metadata": {"reference_id": "not-a-number"},
    })
    assert db._MEMORY_USERS == {}

    # benefit_grant still creates the customer independently of the failed link.
    with patch("cloud.polar.fetch_license_key", new=AsyncMock(return_value="FULLKEY_C")):
        await _handle_benefit_grant({
            "customer_id": "cust_order_c",
            "benefit_id": "benefit_payg",
            "properties": {"license_key_id": "lk_c"},
        })
    assert db._MEMORY_CUSTOMERS["FULLKEY_C"].polar_customer_id == "cust_order_c"


async def test_checkout_updated_non_terminal_status_does_not_link():
    user = await db.get_or_create_user("github", "gh_4", "d@example.com")

    await _handle_checkout_updated({
        "status": "open",
        "customer_id": "cust_order_d",
        "metadata": {"reference_id": str(user.id)},
    })

    untouched = await db.get_user(user.id)
    assert untouched.polar_customer_id is None
    assert untouched.customer_api_key is None


async def test_conflicting_customer_api_key_claim_is_logged_not_raised():
    user1 = await db.get_or_create_user("github", "gh_5a", "e1@example.com")
    user2 = await db.get_or_create_user("github", "gh_5b", "e2@example.com")

    # user1 is already fully linked to a paid customer.
    db._MEMORY_USERS[user1.id] = dataclasses.replace(
        user1, polar_customer_id="cust_shared", customer_api_key="SHARED_KEY"
    )
    db._MEMORY_CUSTOMERS["SHARED_KEY"] = db.Customer(
        api_key="SHARED_KEY", polar_customer_id="cust_shared", credits=100, plan="pro"
    )
    db._MEMORY_BY_POLAR_ID["cust_shared"] = "SHARED_KEY"

    # user2's checkout resolves to the SAME polar_customer_id — the conflict case.
    await _handle_checkout_updated({
        "status": "succeeded",
        "customer_id": "cust_shared",
        "metadata": {"reference_id": str(user2.id)},
    })

    linked_user2 = await db.get_user(user2.id)
    assert linked_user2.polar_customer_id == "cust_shared"
    assert linked_user2.customer_api_key is None  # conflict — not linked, no raise

    linked_user1 = await db.get_user(user1.id)
    assert linked_user1.customer_api_key == "SHARED_KEY"  # unaffected


# ── (o) re-subscription overwrites the stale key; cross-user claim rejected ─


async def test_resubscription_overwrites_customer_api_key_on_benefit_grant_side():
    user = await db.get_or_create_user("github", "gh_6", "f@example.com")

    with patch("cloud.polar.fetch_license_key", new=AsyncMock(return_value="OLD_KEY")):
        await _handle_benefit_grant({
            "customer_id": "cust_resub",
            "benefit_id": "benefit_payg",
            "properties": {"license_key_id": "lk_old"},
        })
    await _handle_checkout_updated({
        "status": "succeeded",
        "customer_id": "cust_resub",
        "metadata": {"reference_id": str(user.id)},
    })
    assert (await db.get_user(user.id)).customer_api_key == "OLD_KEY"

    # Re-subscription: benefit_grant fires again for the SAME customer_id
    # with a NEW license key — must overwrite, not freeze on OLD_KEY.
    with patch("cloud.polar.fetch_license_key", new=AsyncMock(return_value="NEW_KEY")):
        await _handle_benefit_grant({
            "customer_id": "cust_resub",
            "benefit_id": "benefit_payg",
            "properties": {"license_key_id": "lk_new"},
        })

    assert (await db.get_user(user.id)).customer_api_key == "NEW_KEY"


async def test_resubscription_overwrites_customer_api_key_on_checkout_side():
    user = await db.get_or_create_user("github", "gh_7", "g@example.com")

    with patch("cloud.polar.fetch_license_key", new=AsyncMock(return_value="OLD_KEY2")):
        await _handle_benefit_grant({
            "customer_id": "cust_resub2",
            "benefit_id": "benefit_payg",
            "properties": {"license_key_id": "lk_old2"},
        })
    await _handle_checkout_updated({
        "status": "succeeded",
        "customer_id": "cust_resub2",
        "metadata": {"reference_id": str(user.id)},
    })
    assert (await db.get_user(user.id)).customer_api_key == "OLD_KEY2"

    # New checkout completes for the same customer AFTER a fresh benefit_grant
    # already rotated the customers-table key — checkout side must pick up
    # the new value too, not keep coalescing onto the stale one.
    with patch("cloud.polar.fetch_license_key", new=AsyncMock(return_value="NEW_KEY2")):
        await _handle_benefit_grant({
            "customer_id": "cust_resub2",
            "benefit_id": "benefit_payg",
            "properties": {"license_key_id": "lk_new2"},
        })
    await _handle_checkout_updated({
        "status": "succeeded",
        "customer_id": "cust_resub2",
        "metadata": {"reference_id": str(user.id)},
    })

    assert (await db.get_user(user.id)).customer_api_key == "NEW_KEY2"


async def test_cross_user_key_claim_rejected_on_benefit_grant_side():
    user1 = await db.get_or_create_user("github", "gh_8a", "h1@example.com")
    user2 = await db.get_or_create_user("github", "gh_8b", "h2@example.com")

    # Both users are (incorrectly, edge-case) linked to the same polar_customer_id.
    db._MEMORY_USERS[user1.id] = dataclasses.replace(
        user1, polar_customer_id="cust_dup", customer_api_key="KEY_ONE"
    )
    db._MEMORY_USERS[user2.id] = dataclasses.replace(
        user2, polar_customer_id="cust_dup", customer_api_key="KEY_TWO"
    )

    # benefit_grant fires with a key that collides with user1's existing key.
    # user1's row is a same-value no-op; user2's row must be rejected since it
    # would collide with user1's now-current value.
    await db.link_customer_api_key_by_polar_id("cust_dup", "KEY_ONE")

    assert (await db.get_user(user1.id)).customer_api_key == "KEY_ONE"
    assert (await db.get_user(user2.id)).customer_api_key == "KEY_TWO"  # untouched, not overwritten


async def test_first_time_link_via_checkout_still_works_after_coalesce_flip():
    """Base case for link_checkout_to_user's COALESCE flip: a user who has
    never been linked before (customer_api_key is None) must still pick up
    the key on the first checkout.updated, same as before the re-subscription
    fix reordered the COALESCE arguments."""
    user = await db.get_or_create_user("github", "gh_9", "i@example.com")

    with patch("cloud.polar.fetch_license_key", new=AsyncMock(return_value="FIRSTKEY")):
        await _handle_benefit_grant({
            "customer_id": "cust_first",
            "benefit_id": "benefit_payg",
            "properties": {"license_key_id": "lk_first"},
        })
    assert (await db.get_user(user.id)).customer_api_key is None  # not linked yet

    await _handle_checkout_updated({
        "status": "succeeded",
        "customer_id": "cust_first",
        "metadata": {"reference_id": str(user.id)},
    })

    assert (await db.get_user(user.id)).customer_api_key == "FIRSTKEY"
