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
    FROM_EMAIL              "from" address shown to recipients (defaults to noreply@furtuner.com)
    FROM_NAME               optional display name, e.g. "FurTuner"
    REPLY_TO                optional; if set, replies go here (e.g. help@furtuner.com).
                            Leave unset to send as a true no-reply.
    SUPPORT_URL             optional; full https URL of the Support page on the website
                            (opened by the "Support page" link in the email footer)
    EMAIL_LOGO_URL          optional; public https URL of the logo banner PNG used in the
                            email (defaults to https://furtuner.com/images/furtuner-email-logo.png)
"""

import os
import re
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from html import escape as html_escape
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
FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@furtuner.com")
FROM_NAME = os.environ.get("FROM_NAME", "FurTuner")
REPLY_TO = os.environ.get("REPLY_TO", "")  # empty = true no-reply

SITE_URL = "https://furtuner.com"
# Where the "Questions? Visit our Support page" link in the email footer goes.
# The site (App.tsx) opens its Support screen directly when it loads with ?page=support.
SUPPORT_URL = os.environ.get("SUPPORT_URL", f"{SITE_URL}/?page=support")
# Gmail/Outlook can't show SVG, so this must be a PNG hosted at a public URL.
# Drop furtuner-email-logo.png into the site's public/images/ folder.
EMAIL_LOGO_URL = os.environ.get("EMAIL_LOGO_URL", f"{SITE_URL}/images/furtuner-email-logo.png")

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


# ─── Email content ───────────────────────────────────────────────────────
def build_email_bodies(patient_name: str, view_url: str) -> tuple[str, str]:
    """Returns (plain_text, html) for the "your report is ready" email.

    Layout: centered card on a light background -> italic thank-you + message ->
    orange button -> small note -> FurTuner logo banner -> footer. Table-based
    with inline styles on purpose: that's the only layout Gmail/Outlook render
    reliably. patient_name is user input, so it is HTML-escaped before use.
    """
    name = html_escape(patient_name)
    url = html_escape(view_url, quote=True)
    logo = html_escape(EMAIL_LOGO_URL, quote=True)
    support = html_escape(SUPPORT_URL, quote=True)

    plain = (
        "Thank you for visiting FurTuner.\n\n"
        f"Your custom diet report for {patient_name} is ready.\n\n"
        f"View it here: {view_url}\n\n"
        "This link opens in your browser - no download needed.\n\n"
        "--\n"
        f"Questions? Visit our Support page: {SUPPORT_URL}\n"
        "This is an automated message, please do not reply.\n"
        f"FurTuner - {SITE_URL}\n"
    )

    font = "Arial, Helvetica, sans-serif"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
<title>Your FurTuner diet report</title>
</head>
<body style="margin:0; padding:0; background-color:#F3F7FA;">
  <!-- Preview text shown next to the subject in the inbox list -->
  <div style="display:none; max-height:0; overflow:hidden; opacity:0; color:#F3F7FA; font-size:1px; line-height:1px;">
    Thank you for visiting FurTuner &mdash; your custom diet report for {name} is ready.
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F3F7FA" style="background-color:#F3F7FA;">
    <tr>
      <td align="center" style="padding:28px 12px;">

        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF"
               style="width:100%; max-width:600px; margin:0 auto; background-color:#FFFFFF; border-radius:14px; border:1px solid #DCE9F3;">

          <!-- Message -->
          <tr>
            <td align="center" style="padding:44px 36px 8px 36px; font-family:{font}; text-align:center;">
              <p style="margin:0 0 14px 0; font-size:22px; line-height:1.4; font-style:italic; font-weight:700; color:#143C6F;">
                Thank you for visiting FurTuner.
              </p>
              <p style="margin:0; font-size:17px; line-height:1.6; font-style:italic; color:#211915;">
                Your custom diet report for <strong>{name}</strong> is ready.
              </p>
            </td>
          </tr>

          <!-- Button -->
          <tr>
            <td align="center" style="padding:26px 36px 8px 36px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
                <tr>
                  <td align="center" bgcolor="#FA9A36" style="background-color:#FA9A36; border-radius:10px;">
                    <a href="{url}" target="_blank"
                       style="display:inline-block; padding:15px 32px; font-family:{font}; font-size:16px; font-weight:700; color:#211915; text-decoration:none; border-radius:10px;">
                      View {name}&#8217;s Diet Report
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Note -->
          <tr>
            <td align="center" style="padding:14px 36px 36px 36px; font-family:{font}; text-align:center;">
              <p style="margin:0; font-size:13px; line-height:1.5; font-style:italic; color:#6B7785;">
                This link opens in your browser &mdash; no download needed.
              </p>
            </td>
          </tr>

          <!-- Logo banner (light-blue bar is baked into the PNG so it looks right in dark mode too) -->
          <tr>
            <td align="center" bgcolor="#E8F5FF"
                style="background-color:#E8F5FF; border-radius:0 0 13px 13px; font-family:{font}; font-size:26px; font-weight:700; color:#143C6F; line-height:1;">
              <img src="{logo}" width="600" alt="FurTuner"
                   style="display:block; width:100%; max-width:600px; height:auto; border:0; outline:none; text-decoration:none; border-radius:0 0 13px 13px;">
            </td>
          </tr>

        </table>

        <!-- Footer -->
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%; max-width:600px; margin:0 auto;">
          <tr>
            <td align="center" style="padding:20px 20px 0 20px; font-family:{font}; font-size:12px; line-height:1.7; color:#6B7785; text-align:center;">
              Questions? Visit our <a href="{support}" target="_blank" style="color:#143C6F; text-decoration:underline;">Support page</a><br>
              This is an automated message, please do not reply.<br>
              <a href="{SITE_URL}" style="color:#143C6F; text-decoration:underline;">furtuner.com</a>
            </td>
          </tr>
        </table>

      </td>
    </tr>
  </table>
</body>
</html>"""
    return plain, html


# ─── Sending ─────────────────────────────────────────────────────────────
def send_report_link_email(to_address: str, subject: str, plain_body: str, html_body: str) -> None:
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError(
            "SMTP is not configured — set SMTP_HOST, SMTP_USER, SMTP_PASSWORD "
            "(and optionally SMTP_PORT, FROM_EMAIL, FROM_NAME) as environment variables."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((FROM_NAME, FROM_EMAIL))  # -> FurTuner <noreply@furtuner.com>
    msg["To"] = to_address
    if REPLY_TO:
        msg["Reply-To"] = REPLY_TO
    # Explicit UTF-8 so accents / dashes / curly quotes can never be mis-decoded.
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

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

    # Collapse whitespace/newlines so a pet name can never inject extra email
    # headers via the Subject line, and keep it a sensible length.
    clean_name = " ".join(req.patient_name.split())[:80] or "Your Pet"
    subject = f"Diet Report for {clean_name}"

    plain_body, html_body = build_email_bodies(clean_name, view_url)

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
    #
    # IMPORTANT: use resp.content (raw bytes) and decode as UTF-8 ourselves.
    # resp.text would guess the charset, and `requests` falls back to
    # ISO-8859-1 whenever the response has no explicit charset. That silently
    # turned UTF-8 bytes for "•" and "—" into "â¢" / "â" (mojibake).
    return HTMLResponse(content=resp.content.decode("utf-8", errors="replace"))


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
