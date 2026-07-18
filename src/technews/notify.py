"""Notify agent: send the daily digest by email via Resend.

Resend was chosen over Gmail SMTP: a single ``RESEND_API_KEY`` instead of a
stored app password, better deliverability for automated daily mail, a clean
HTTP API (one POST), and a generous free tier where self-delivery works without
a custom domain. The send is gated on the key being present; ``--dry-run`` (or a
missing key) renders the HTML without sending.
"""

from __future__ import annotations

import logging
import os

import httpx

from .models import Edition
from .render import render_email

log = logging.getLogger("technews.notify")

RESEND_ENDPOINT = "https://api.resend.com/emails"


def send_digest(edition: Edition, settings: dict, dry_run: bool = False) -> dict:
    """Render and (unless dry-run / no key) send the digest.

    Returns a small status dict describing what happened.
    """
    email_cfg = settings.get("email", {})
    html = render_email(edition)
    subject = f"{email_cfg.get('subject_prefix', 'Tech Politics — Daily Top 5')} · {edition.date}"

    api_key = os.environ.get("RESEND_API_KEY")
    if dry_run or not api_key:
        reason = "dry-run" if dry_run else "no RESEND_API_KEY"
        log.info("email not sent (%s); rendered %d chars", reason, len(html))
        return {"sent": False, "reason": reason, "subject": subject, "html": html}

    try:
        resp = httpx.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "from": email_cfg.get("from", "onboarding@resend.dev"),
                "to": [email_cfg["to"]],
                "subject": subject,
                "html": html,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        message_id = resp.json().get("id")
        log.info("email sent to %s (id=%s)", email_cfg.get("to"), message_id)
        return {"sent": True, "id": message_id, "subject": subject}
    except Exception as exc:  # noqa: BLE001 — never let mail failure sink a run
        log.warning("email send failed: %s", exc)
        return {"sent": False, "reason": str(exc), "subject": subject}
