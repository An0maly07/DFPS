"""Alert delivery: Firebase Cloud Messaging (HTTP v1) and Twilio WhatsApp Sandbox.

Each channel is only *configured* when its credentials are present in .env; dispatch()
reports exactly what happened per subscriber rather than pretending delivery.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from backend.config import settings

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def channels_configured() -> list[str]:
    out = []
    if settings.firebase_key_path and settings.firebase_key_path.exists():
        out.append("fcm")
    if settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_whatsapp_from:
        out.append("whatsapp")
    return out


def _mask(identifier: str) -> str:
    return identifier if len(identifier) <= 6 else identifier[:3] + "…" + identifier[-3:]


def send_whatsapp(to: str, body: str) -> dict[str, Any]:
    to = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        data={"From": settings.twilio_whatsapp_from, "To": to, "Body": body},
        timeout=20,
    )
    ok = r.status_code in (200, 201)
    payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return {"ok": ok, "status_code": r.status_code, "sid": payload.get("sid"), "error": None if ok else payload.get("message", r.text[:200])}


def send_fcm(token: str, title: str, body: str, data: dict[str, str] | None = None) -> dict[str, Any]:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    key_path = settings.firebase_key_path
    creds = service_account.Credentials.from_service_account_file(str(key_path), scopes=[FCM_SCOPE])
    creds.refresh(Request())
    project_id = json.loads(key_path.read_text())["project_id"]
    msg = {"message": {"token": token, "notification": {"title": title, "body": body}, "data": data or {}}}
    r = requests.post(
        f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
        json=msg,
        timeout=20,
    )
    ok = r.status_code == 200
    return {"ok": ok, "status_code": r.status_code, "error": None if ok else r.text[:300]}


def dispatch(subscribers: list[dict[str, Any]], title: str, body: str, dry_run: bool = False) -> list[dict[str, Any]]:
    configured = channels_configured()
    results = []
    for s in subscribers:
        ch, ident = s["channel"], s["identifier"]
        entry: dict[str, Any] = {"channel": ch, "identifier": _mask(ident)}
        if ch not in configured:
            entry.update(status="skipped", detail=f"{ch} not configured (missing credentials in .env)")
        elif dry_run:
            entry.update(status="dry_run", detail="would send")
        else:
            try:
                res = send_whatsapp(ident, f"{title}\n{body}") if ch == "whatsapp" else send_fcm(ident, title, body)
                entry.update(status="sent" if res["ok"] else "failed", detail=res)
            except Exception as e:  # network / auth errors are reported, not raised
                entry.update(status="failed", detail=str(e)[:300])
        results.append(entry)
    return results
