"""
report_email_router.py

Handles POST /report/email — receives the *exact* HTML the frontend's
"Print / Save" button renders (captured from the .print-only DOM node)
and emails it as an attached .html file. No PDF, no Chromium, no
third-party rendering service — just Gmail SMTP, already configured.

WHY AN ATTACHED FILE INSTEAD OF THE EMAIL BODY: email clients (Gmail,
Outlook, etc.) don't render HTML with a real browser engine — they run it
through a restrictive sanitizer that strips or limits a lot of CSS for
security reasons, which is why the pie chart and other visual elements
didn't render correctly when the report was sent as the email body
itself. An attached .html file has no such restriction: the recipient
double-clicks it, it opens in their actual browser, and renders exactly
like the site's own "Print / Save" output — same engine, same CSS
support, pixel-identical.

─── Wire this into each of your 6 FastAPI apps ──────────────────────────
    from report_email_router import router as report_email_router
    app.include_router(report_email_router)

─── requirements.txt ─────────────────────────────────────────────────────
    fastapi
    pydantic

─── Environment variables ────────────────────────────────────────────────
    SMTP_HOST       e.g. smtp.gmail.com
    SMTP_PORT       e.g. 587
    SMTP_USER       the SMTP username
    SMTP_PASSWORD   the SMTP password / app password / API key
    FROM_EMAIL      "from" address shown to recipients (defaults to SMTP_USER)
    FROM_NAME       optional display name, e.g. "FurTuner"
"""

import os
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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


# ─── Sending ─────────────────────────────────────────────────────────────
def send_email_with_html_attachment(
    to_address: str, subject: str, body_text: str, html_bytes: bytes, filename: str
) -> None:
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError(
            "SMTP is not configured — set SMTP_HOST, SMTP_USER, SMTP_PASSWORD "
            "(and optionally SMTP_PORT, FROM_EMAIL, FROM_NAME) as environment variables."
        )

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_address
    msg.attach(MIMEText(body_text, "plain"))

    attachment = MIMEApplication(html_bytes, _subtype="html")
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)

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

    safe_name = "".join(c for c in req.patient_name if c.isalnum() or c in " -_").strip() or "Diet-Report"
    filename = f"{safe_name} - Diet Report.html"
    subject = f"Diet Report for {req.patient_name}"
    body_text = (
        f"Your diet report for {req.patient_name} is attached.\n\n"
        "Open the attached file in a web browser (double-click it, or right-click "
        "and choose 'Open with' your browser) to view the full report."
    )

    try:
        send_email_with_html_attachment(
            req.recipient_email, subject, body_text, req.report_html.encode("utf-8"), filename
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except smtplib.SMTPException as e:
        raise HTTPException(status_code=502, detail=f"Email provider rejected the send: {e}")

    return {"ok": True}
