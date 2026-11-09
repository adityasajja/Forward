"""Broker login and its callback."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from forward.api.deps import get_principal
from forward.services.broker_session import BrokerSessionService
from forward.wiring.broker_data import get_broker_session_service

router = APIRouter()


# ----------------------------------------------------------------------
# Session / login
# ----------------------------------------------------------------------
class LoginIn(BaseModel):
    client_id: str
    auth_code: str


@router.get("/login/status")
def login_status(
    service: BrokerSessionService = Depends(get_broker_session_service),
) -> dict[str, Any]:
    return service.status()


@router.post("/login")
def login_submit(
    body: LoginIn,
    service: BrokerSessionService = Depends(get_broker_session_service),
) -> dict[str, Any]:
    """Manual login: exchange client_id and auth_code for a session."""
    try:
        session = service.login(body.client_id.strip(), body.auth_code.strip())
    except Exception as exc:  # noqa: BLE001 - surface IIFL error text
        raise HTTPException(400, f"login failed: {exc}") from exc
    return {
        "session_active": True,
        "client_id": session["client_id"],
        "expires_at": session["expires_at"],
    }


@router.post("/logout", dependencies=[Depends(get_principal)])
def logout_broker(
    service: BrokerSessionService = Depends(get_broker_session_service),
) -> dict[str, bool]:
    """Forget the saved broker session, so the app stops trading as you until the next login.

    This clears the app's own copy of the token. The broker's token itself is left to
    expire at midnight. The System page's "log out" button called this route, but it did
    not exist, so the request fell through to the web app and came back "Method Not Allowed".
    """
    return {"logged_out": service.logout()}


_LOGIN_CALLBACK_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Forward — login</title>
<style>
  body { font-family: -apple-system, Segoe UI, Inter, sans-serif; background: #0f1319; color: #e6e9ef;
         display: grid; place-items: center; min-height: 100vh; margin: 0; }
  .card { background: #161b22; border: 1px solid #26d3; border-radius: 12px; padding: 28px 36px; max-width: 420px; }
  h1 { margin: 0 0 8px; font-size: 20px; }
  p { color: #9aa4b2; margin: 6px 0; }
  .ok { color: #26a69a; }
  .err { color: #ef5350; }
  button { margin-top: 14px; padding: 8px 16px; border: 0; border-radius: 8px; background: #635bff; color: white; cursor: pointer; }
</style>
<script>setTimeout(function() {{ if (window.opener) window.close(); }}, 1200);</script>
</head>
<body>
"""


@router.get("/login/callback")
def login_callback(
    # IIFL redirects with the lowercase, unseparated names.
    authcode: str | None = Query(None),
    clientid: str | None = Query(None),
    service: BrokerSessionService = Depends(get_broker_session_service),
) -> HTMLResponse:
    """Landing page for the IIFL redirect. Exchanges the code for a session.

    Declaring these as required `client_id`/`auth_code` meant every real
    redirect came back 422 "Field required" as raw JSON, and no session was
    ever created — the login could not complete by any route through the
    browser.
    """
    if not authcode or not clientid:
        return HTMLResponse(
            _LOGIN_CALLBACK_HTML
            + '<div class="card"><h1>Login failed</h1>'
            + '<p class="err">The redirect did not carry a client id and auth code.</p>'
            + "<p>Expected <code>?authcode=...&amp;clientid=...</code>, which is what "
            + "markets.iiflcapital.com sends. If you opened this URL by hand, check "
            + "the parameter names.</p></div>",
            status_code=400,
        )

    try:
        session = service.login(clientid, authcode)
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(
            _LOGIN_CALLBACK_HTML
            + '<div class="card"><h1>Login failed</h1><p class="err">'
            + f"{escape(str(exc))}</p>"
            + "<p>Close this tab and try again from the dashboard.</p></div>",
            status_code=400,
        )
    return HTMLResponse(
        _LOGIN_CALLBACK_HTML
        + '<div class="card"><h1 class="ok">Logged in ✓</h1>'
        + f"<p>Client: <strong>{escape(str(session['client_id']))}</strong></p>"
        + f"<p>Expires: <strong>{datetime.fromisoformat(session['expires_at']).strftime('%d-%b-%Y %H:%M')} IST</strong></p>"
        + "<p>Taking you back to the dashboard…</p>"
        + "<a href='/'><button>Go now</button></a></div>"
        # Back to the dashboard on its own. If the dashboard opened this tab, it is still open
        # behind it, so close this one; otherwise (a bookmarked or typed login URL) go to it.
        + "<noscript><meta http-equiv='refresh' content='1;url=/'></noscript>"
        + "<script>setTimeout(function () {"
        + " if (window.opener && !window.opener.closed) { window.close(); }"
        + " else { location.replace('/'); }"
        + " }, 1200);</script>"
    )
