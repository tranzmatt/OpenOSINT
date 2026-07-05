"""GET /checkout/return — post-payment landing page.

Polls GET /v1/me client-side until `linked` flips true (the webhook
rendezvous completes asynchronously, so it isn't guaranteed to be done by
the time the browser lands here). After a timeout it stops polling and
shows a fallback message.

Same static-HTML-only rule as cloud/routes/dashboard.py: no user data is
interpolated server-side, only fetched and rendered client-side.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from cloud.session_auth import get_current_user

router = APIRouter()

_RETURN_HTML = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>OpenOSINT Cloud — Finishing up</title></head>
<body>
<h1>Finishing up your purchase…</h1>
<p id="status">Checking…</p>
<div id="fallback" style="display:none">
  <p>Still not linked? <a href="/dashboard">Go to your dashboard</a> and try again in a minute.</p>
</div>
<script>
const POLL_INTERVAL_MS = 2000;
const MAX_ATTEMPTS = 15;

async function poll(attempt) {
  const resp = await fetch("/v1/me");
  if (resp.ok) {
    const me = await resp.json();
    if (me.linked) {
      document.getElementById("status").textContent = "Done — your account is linked.";
      return;
    }
  }
  if (attempt >= MAX_ATTEMPTS) {
    document.getElementById("status").textContent = "Still waiting on confirmation.";
    document.getElementById("fallback").style.display = "block";
    return;
  }
  setTimeout(() => poll(attempt + 1), POLL_INTERVAL_MS);
}

poll(0);
</script>
</body>
</html>"""


@router.get("/checkout/return", response_class=HTMLResponse)
async def checkout_return(user=Depends(get_current_user)) -> HTMLResponse:
    return HTMLResponse(_RETURN_HTML)
