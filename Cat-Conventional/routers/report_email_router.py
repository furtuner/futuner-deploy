"""
report_email_router.py

Handles POST /report/email — receives the *exact* HTML the frontend's
"Print / Save" button renders (captured from the .print-only DOM node)
and emails it directly as the HTML body of the email itself. No PDF
rendering, no Chromium, no third-party PDF service — just Gmail SMTP,
which is already configured.

(Previously this rendered the HTML to a PDF via a Node function running
headless Chromium on Vercel, which repeatedly failed with
"libnss3.so: cannot open shared object file" — a persistent environment
mismatch between Vercel's runtime and what Chromium expects. Sending the
report as the email body itself sidesteps that whole category of problem:
the recipient sees the full formatted report the moment they open the
email, no attachment to download.)

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
def send_html_email(to_address: str, subject: str, html_body: str, plain_fallback: str) -> None:
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError(
            "SMTP is not configured — set SMTP_HOST, SMTP_USER, SMTP_PASSWORD "
            "(and optionally SMTP_PORT, FROM_EMAIL, FROM_NAME) as environment variables."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_address

    # Plain-text fallback first, then HTML — email clients prefer the last
    # part they understand, so HTML (richer) goes last.
    msg.attach(MIMEText(plain_fallback, "plain"))
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

    subject = f"Diet Report for {req.patient_name}"
    plain_fallback = (
        f"Your diet report for {req.patient_name} is ready. "
        "Please view this email in an HTML-capable email client to see the full report."
    )

    try:
        send_html_email(req.recipient_email, subject, req.report_html, plain_fallback)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except smtplib.SMTPException as e:
        raise HTTPException(status_code=502, detail=f"Email provider rejected the send: {e}")

    return {"ok": True}
