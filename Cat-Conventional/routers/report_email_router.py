"""
report_email_router.py

Handles POST /report/email — receives the *exact* HTML the frontend's
"Print / Save" button renders (captured from the .print-only DOM node),
uploads it to Vercel Blob storage as a public .html file, and emails the
recipient a LINK to it instead of an attachment.

WHY A LINK INSTEAD OF AN ATTACHMENT OR EMBEDDED HTML: email clients
render HTML through a restrictive sanitizer (this is what broke the pie
chart when the report was sent as the email body). An attachment avoids
that, but some email clients flag .html attachments as risky, and older/
less technical recipients can find "open this attachment in a browser"
confusing. A plain link is the simplest possible experience: the
recipient clicks it, and their own browser renders the actual file with
zero restrictions — no attachment, no download step, no "open with"
menu. It's also exactly what most portals (Stripe, medical results
portals, etc.) do for this kind of "your report is ready" email.

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
    vercel_blob

─── Environment variables ────────────────────────────────────────────────
    BLOB_READ_WRITE_TOKEN   auto-added when you create a Blob store (see Setup)
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
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import vercel_blob
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

router = APIRouter()

# ─── Config ──────────────────────────────────────────────────────────────
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER)
FROM_NAME = os.environ.get("FROM_NAME", "FurTuner")

MAX_HTML_BYTES = 5_000_000  # ~5MB sanity cap on the incoming report HTML


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
    # Plain-text fallback first, HTML last — email clients prefer the last
    # part they understand, so the richer HTML version wins when supported.
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(FROM_EMAIL, [to_address], msg.as_string())


# ─── Route ───────────────────────────────────────────────────────────────
@router.post("/report/email")
def email_report(req: ReportEmailRequest):
    if len(req.report_html.encode("utf-8")) > MAX_HTML_BYTES:
        raise HTTPException(status_code=413, detail="Report HTML too large.")

    try:
        report_url = upload_report(req.report_html, req.patient_name)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    subject = f"Diet Report for {req.patient_name}"

    # Plain-text fallback (shown by clients that can't render HTML emails,
    # or when a recipient's client prefers plain text) — includes the raw
    # URL, since there's no way to make "click here" clickable without HTML.
    plain_body = (
        f"Your diet report for {req.patient_name} is ready.\n\n"
        f"View it here: {report_url}\n\n"
        "This link opens directly in your browser — no download needed."
    )

    # HTML version — this is what most recipients will actually see:
    # a short message with "click here" as the clickable link text.
    html_body = f"""
    <div style="font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #211915; line-height: 1.6;">
      <p>Your diet report for <strong>{req.patient_name}</strong> is ready — 
      <a href="{report_url}" style="color: #143C6F; font-weight: 700;">click here to view it</a>.</p>
      <p style="color: #666; font-size: 13px;">This link opens directly in your browser — no download needed.</p>
    </div>
    """

    try:
        send_report_link_email(req.recipient_email, subject, plain_body, html_body)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except smtplib.SMTPException as e:
        raise HTTPException(status_code=502, detail=f"Email provider rejected the send: {e}")


    return {"ok": True, "report_url": report_url}
