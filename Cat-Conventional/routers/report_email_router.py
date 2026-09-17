"""
report_email_router.py

Handles POST /report/email — receives the *exact* HTML the frontend's
"Print / Save" button renders (captured from the .print-only DOM node),
turns it into a PDF, and emails that PDF as an attachment.

─── How the PDF gets made (no third-party account) ──────────────────────
This calls /api/render-pdf — a Node.js serverless function living in the
SAME Vercel project (see api/render-pdf.js) that uses real headless
Chromium via @sparticuz/chromium-min + puppeteer-core.

─── Wire this into each of your 6 FastAPI apps ──────────────────────────
    from report_email_router import router as report_email_router
    app.include_router(report_email_router)

─── requirements.txt ─────────────────────────────────────────────────────
    fastapi
    pydantic
    requests

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

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

router = APIRouter()

_VERCEL_URL = os.environ.get("VERCEL_URL")
RENDER_PDF_URL = f"https://{_VERCEL_URL}/api/render-pdf" if _VERCEL_URL else "http://localhost:3000/api/render-pdf"

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER)
FROM_NAME = os.environ.get("FROM_NAME", "FurTuner")

MAX_HTML_BYTES = 5_000_000


class ReportEmailRequest(BaseModel):
    recipient_email: EmailStr
    patient_name: str
    report_html: str


def render_pdf(html: str) -> bytes:
    resp = requests.post(RENDER_PDF_URL, json={"html": html}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"render-pdf function returned {resp.status_code}: {resp.text[:300]}")
    return resp.content


def send_email_with_pdf(to_address: str, subject: str, body_text: str, pdf_bytes: bytes, filename: str) -> None:
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

    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(FROM_EMAIL, [to_address], msg.as_string())


@router.post("/report/email")
def email_report(req: ReportEmailRequest):
    if len(req.report_html.encode("utf-8")) > MAX_HTML_BYTES:
        raise HTTPException(status_code=413, detail="Report HTML too large.")

    try:
        pdf_bytes = render_pdf(req.report_html)
    except (requests.RequestException, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=f"Couldn't render the report to PDF: {e}")

    safe_name = "".join(c for c in req.patient_name if c.isalnum() or c in " -_").strip() or "Diet-Report"
    filename = f"{safe_name} - Diet Report.pdf"
    subject = f"Diet Report for {req.patient_name}"
    body_text = "Your diet report is attached as a PDF."

    try:
        send_email_with_pdf(req.recipient_email, subject, body_text, pdf_bytes, filename)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except smtplib.SMTPException as e:
        raise HTTPException(status_code=502, detail=f"Email provider rejected the send: {e}")

    return {"ok": True}
