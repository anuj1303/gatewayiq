"""Gmail send helper for GatewayIQ weekly reports.

Same pattern as the Databricks Workshop Hub: a stored OAuth refresh token
(gcloud public client) is exchanged for an access token at send time, then we
POST the MIME message to the Gmail REST API. The word cloud is embedded as an
inline CID image (multipart/related) so it renders in Gmail (data: URIs get
stripped by most clients).

Credentials come from env (set from the `gatewayiq` Databricks secret scope):
  GMAIL_REFRESH_TOKEN, GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GOOGLE_QUOTA_PROJECT
"""
import os
import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

import requests

try:
    from . import config as cfg
except ImportError:
    import config as cfg

log = logging.getLogger("gatewayiq.gmail")

# gcloud CLI public OAuth client (same one the Workshop Hub uses).
CLIENT_ID = os.environ.get("GMAIL_CLIENT_ID",
                           "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com")
CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "d-FL95Q19q7MQmFpd7hHD0Ty")
QUOTA_PROJECT = cfg.GOOGLE_QUOTA_PROJECT

CID = "gatewayiq_wordcloud"          # matches the cid: in report_email when sending


def configured():
    return bool(os.environ.get("GMAIL_REFRESH_TOKEN"))


def profile(refresh_token=None):
    """Validate the token and return the Gmail profile (emailAddress). Does NOT
    send anything — used for a safe 'is Gmail connected?' check."""
    token = _access_token(refresh_token or os.environ["GMAIL_REFRESH_TOKEN"])
    r = requests.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", timeout=15,
                     headers={"Authorization": f"Bearer {token}", "x-goog-user-project": QUOTA_PROJECT})
    if r.status_code != 200:
        raise RuntimeError(f"Gmail profile check failed ({r.status_code}): {r.text[:200]}")
    return r.json()


def _access_token(refresh_token):
    r = requests.post("https://oauth2.googleapis.com/token", timeout=20, data={
        "grant_type": "refresh_token", "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET, "refresh_token": refresh_token})
    if r.status_code != 200:
        raise RuntimeError(f"Gmail token refresh failed ({r.status_code}): {r.text[:200]}")
    return r.json()["access_token"]


def send_html(*, to_email, subject, html, from_name=None,
              from_email=None, wordcloud_b64=None,
              refresh_token=None, access_token=None):
    from_name = from_name or cfg.MAIL_FROM_NAME
    from_email = from_email or cfg.MAIL_FROM_EMAIL
    """Send one HTML email (optionally with an inline word cloud). Raises on failure."""
    token = access_token or _access_token(refresh_token or os.environ["GMAIL_REFRESH_TOKEN"])

    inner = MIMEText(html, "html", "utf-8")
    if wordcloud_b64:
        msg = MIMEMultipart("related")
        msg.attach(inner)
        img = MIMEImage(base64.b64decode(wordcloud_b64), _subtype="png")
        img.add_header("Content-ID", f"<{CID}>")
        img.add_header("Content-Disposition", "inline", filename="wordcloud.png")
        msg.attach(img)
    else:
        msg = inner

    msg["To"] = to_email
    msg["From"] = f"{from_name} <{from_email}>"
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    r = requests.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                      timeout=30, json={"raw": raw},
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json",
                               "x-goog-user-project": QUOTA_PROJECT})
    if r.status_code != 200:
        try:
            err = r.json().get("error", {}).get("message", r.text[:200])
        except Exception:
            err = r.text[:200]
        raise RuntimeError(f"Gmail send failed ({r.status_code}): {err}")
    return {"status": "sent", "id": r.json().get("id", ""), "to": to_email}
