"""
report_email_router.py

Handles POST /report/email — receives the *exact* HTML the frontend's
"Print / Save" button renders (captured from the .print-only DOM node),
uploads it to Vercel Blob storage, and emails the recipient a LINK to
view it — served through this backend's own /report/view route rather
than Blob's own public URL directly.

WHY A LINK INSTEAD OF AN ATTACHMENT OR EMBEDDED HTML: email clients
render HTML through a restrictive sanitizer (this is what broke the pie
chart when the report was sent as the email body). A plain link is the
simplest possible experience: the recipient clicks it, and their own
browser renders the actual file with zero restrictions.

WHY /report/view PROXIES THE BLOB INSTEAD OF LINKING TO IT DIRECTLY:
Vercel Blob intentionally forces "Content-Disposition: attachment" for
HTML files specifically (to stop people hosting websites on Blob
storage) — there's no option to override this. Linking straight to the
Blob URL would force a download instead of rendering in the browser.
Fetching the file server-side here and returning it as a normal
HTMLResponse sidesteps that entirely: FastAPI's response has no forced
attachment header, so it renders inline exactly like any other webpage.

─── Setup ─────────────────────────────────────────────────────────────
    1. In this Vercel project: Storage tab → Create Database → Blob →
       access: Public. This auto-adds BLOB_READ_WRITE_TOKEN as an
       environment variable — nothing to copy/paste manually.
    2. Add "vercel_blob" to requirements.txt.

─── Wire this into each of your 6 FastAPI apps ──────────────────────────
    from report_email_router import router as report_email_router
    app.include_router(report_email_router)

─── requirements.txt ─────────────────────────────────────────────────────
    fastapi
    pydantic
    requests
    vercel_blob

─── Environment variables ────────────────────────────────────────────────
    BLOB_READ_WRITE_TOKEN   auto-added when you create a Blob store (see Setup)
    VERCEL_URL              set automatically by Vercel for every deployment
    SMTP_HOST               e.g. smtp.gmail.com
    SMTP_PORT               e.g. 587
    SMTP_USER               the SMTP username
    SMTP_PASSWORD           the SMTP password / app password / API key
    FROM_EMAIL              "from" address shown to recipients (defaults to SMTP_USER)
    FROM_NAME               optional display name, e.g. "FurTuner"
"""

import os
import re
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote, urlparse

import requests
import vercel_blob
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr

router = APIRouter()

# ─── Config ──────────────────────────────────────────────────────────────
_VERCEL_URL = os.environ.get("VERCEL_URL")
BASE_URL = f"https://{_VERCEL_URL}" if _VERCEL_URL else "http://localhost:8000"

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER)
FROM_NAME = os.environ.get("FROM_NAME", "FurTuner")

MAX_HTML_BYTES = 5_000_000  # ~5MB sanity cap on the incoming report HTML
RETENTION_DAYS = 30  # reports older than this are auto-deleted by /report/cleanup

# Only ever proxy URLs on Vercel Blob's own domain — this route fetches
# and returns whatever URL it's given, so without this check it would be
# an open proxy anyone could use to fetch arbitrary URLs through your server.
ALLOWED_BLOB_HOST_SUFFIX = ".public.blob.vercel-storage.com"


# ─── Request schema ──────────────────────────────────────────────────────
class ReportEmailRequest(BaseModel):
    recipient_email: EmailStr
    patient_name: str
    report_html: str


# ─── Upload the report and get a public URL back ─────────────────────────
def upload_report(html: str, patient_name: str) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9-_]", "-", patient_name).strip("-") or "diet-report"
    pathname = f"reports/{safe_name}-{uuid.uuid4().hex[:8]}.html"

    try:
        resp = vercel_blob.put(
            pathname,
            html.encode("utf-8"),
            {"contentType": "text/html; charset=utf-8", "addRandomSuffix": "false"},
        )
    except Exception as e:  # vercel_blob doesn't document a narrow exception type
        raise RuntimeError(f"Couldn't upload the report: {e}")

    url = resp.get("url") if isinstance(resp, dict) else None
    if not url:
        raise RuntimeError(f"Upload succeeded but no URL was returned: {resp}")
    return url


# ─── Sending ─────────────────────────────────────────────────────────────
def send_report_link_email(to_address: str, subject: str, plain_body: str, html_body: str) -> None:
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError(
            "SMTP is not configured — set SMTP_HOST, SMTP_USER, SMTP_PASSWORD "
            "(and optionally SMTP_PORT, FROM_EMAIL, FROM_NAME) as environment variables."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_address
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(FROM_EMAIL, [to_address], msg.as_string())


# ─── Route: send the email ────────────────────────────────────────────────
@router.post("/report/email")
def email_report(req: ReportEmailRequest):
    if len(req.report_html.encode("utf-8")) > MAX_HTML_BYTES:
        raise HTTPException(status_code=413, detail="Report HTML too large.")

    try:
        blob_url = upload_report(req.report_html, req.patient_name)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Link to OUR OWN /report/view route (which proxies the Blob content),
    # not the Blob URL directly — see the module docstring for why.
    view_url = f"{BASE_URL}/report/view?src={quote(blob_url, safe='')}"

    subject = f"Diet Report for {req.patient_name}"

    plain_body = (
        f"Your diet report for {req.patient_name} is ready.\n\n"
        f"View it here: {view_url}\n\n"
        "This link opens directly in your browser — no download needed."
    )

    html_body = f"""
    <div style="font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #211915; line-height: 1.6;">
      <p>Your diet report for <strong>{req.patient_name}</strong> is ready — 
      <a href="{view_url}" style="color: #143C6F; font-weight: 700;">click here to view it</a>.</p>
      <p style="color: #666; font-size: 13px;">This link opens directly in your browser — no download needed.</p>
    </div>
    """

    try:
        send_report_link_email(req.recipient_email, subject, plain_body, html_body)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except smtplib.SMTPException as e:
        raise HTTPException(status_code=502, detail=f"Email provider rejected the send: {e}")

    return {"ok": True, "view_url": view_url}


# ─── Route: view the report inline ────────────────────────────────────────
@router.get("/report/view", response_class=HTMLResponse)
def view_report(src: str = Query(...)):
    parsed = urlparse(src)
    if parsed.scheme != "https" or not parsed.netloc.endswith(ALLOWED_BLOB_HOST_SUFFIX):
        raise HTTPException(status_code=400, detail="Invalid report source.")

    try:
        resp = requests.get(src, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Couldn't load the report: {e}")

    # No Content-Disposition set here at all, so the browser renders this
    # inline like any normal webpage — this is the whole point of proxying
    # through here instead of linking straight to the Blob URL.
    return HTMLResponse(content=resp.text)


# ─── Route: delete reports older than RETENTION_DAYS ─────────────────────
# Call this on a schedule via a Vercel cron job — add to vercel.json:
#   { "path": "/report/cleanup", "schedule": "0 3 * * *" }   (daily at 3am UTC)
@router.get("/report/cleanup")
def cleanup_old_reports(authorization: str = Header(default="")):
    # Vercel auto-provides CRON_SECRET and sends it as this header when it
    # invokes a cron job — checking it stops anyone else who finds this
    # URL from triggering a mass-deletion of reports on demand.
    cron_secret = os.environ.get("CRON_SECRET", "")
    if cron_secret and authorization != f"Bearer {cron_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized.")

    cutoff = datetime.now(timezone.utc).timestamp() - (RETENTION_DAYS * 86400)
    deleted = []
    cursor = None

    while True:
        opts = {"prefix": "reports/", "limit": "1000"}
        if cursor:
            opts["cursor"] = cursor

        try:
            listing = vercel_blob.list(opts)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Couldn't list reports: {e}")

        blobs = listing.get("blobs", [])
        to_delete = []
        for blob in blobs:
            uploaded_at_raw = blob.get("uploadedAt")
            if not uploaded_at_raw:
                continue
            # uploadedAt comes back as an ISO 8601 string; handle a
            # trailing "Z" (UTC) for compatibility with Python < 3.11.
            uploaded_at_str = uploaded_at_raw.replace("Z", "+00:00") if isinstance(uploaded_at_raw, str) else None
            if not uploaded_at_str:
                continue
            try:
                uploaded_ts = datetime.fromisoformat(uploaded_at_str).timestamp()
            except ValueError:
                continue
            if uploaded_ts < cutoff:
                to_delete.append(blob["url"])

        if to_delete:
            vercel_blob.delete(to_delete)
            deleted.extend(to_delete)

        cursor = listing.get("cursor")
        if not listing.get("hasMore") or not cursor:
            break

    return {"deleted_count": len(deleted)}
