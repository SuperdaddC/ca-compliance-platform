"""
AWS Lambda Handler — Compliance Platform
Routes requests to scan, checkout, and Stripe webhook handlers.

Routes:
  POST /scan        — scrape URL + run compliance rule engine
  POST /checkout    — create Stripe Checkout session (returns redirect URL)
  POST /webhook     — Stripe webhook: unlock paid tier after payment
  OPTIONS *         — CORS preflight
"""

import json
import os
import traceback
import hashlib
import hmac
import urllib.request
import urllib.parse
import base64
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright
from supabase import create_client, Client
from rule_engine import check_compliance

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "ComplyWithJudy <onboarding@resend.dev>")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://complywithjudy.com")

# Mapping price IDs → tier name stored in Supabase
PRICE_TIER_MAP = {
    "price_1TAxj8Rxek7i9f8M38qngJUD": "single",   # $19 Single Scan
    "price_1TAxj9Rxek7i9f8MZt4Nt5kO": "fix_verify", # $39 Fix & Verify
    "price_1TAxj9Rxek7i9f8MvOjyUlss": "pro",       # $39/mo
    "price_1TAxjIRxek7i9f8MSi8s1NlO": "pro",       # $374/yr
    "price_1TAxjARxek7i9f8MAVtqXhUb": "broker",    # $199/mo
    "price_1TAxjIRxek7i9f8M5rrM6q2s": "broker",    # $1910/yr
}

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _stripe_request(method: str, path: str, params: dict = None) -> dict:
    """Make a raw HTTP request to the Stripe API (no stripe-python dependency needed)."""
    url = f"https://api.stripe.com{path}"
    auth = base64.b64encode(f"{STRIPE_SK}:".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = urllib.parse.urlencode(params or {}).encode() if params else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        raise RuntimeError(f"Stripe error {e.code}: {body.get('error', {}).get('message', str(body))}")


def _send_email(to: str, subject: str, html: str) -> bool:
    """Send an email via Resend API. Returns True on success."""
    if not RESEND_API_KEY or not to:
        return False
    try:
        payload = json.dumps({
            "from": RESEND_FROM,
            "to": [to],
            "subject": subject,
            "html": html,
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status < 300
    except Exception as e:
        print(f"Email send failed: {e}")
        return False


def _build_results_email(scan: dict, paid: bool) -> str:
    """Build HTML email for scan results (free = score summary, paid = full report link)."""
    url = scan.get("url", "")
    score = scan.get("score", 0)
    scan_id = scan.get("id", "")
    results = scan.get("results") or {}
    checks = results.get("checks", []) if isinstance(results, dict) else []

    passed   = sum(1 for c in checks if c.get("status") == "pass")
    warnings = sum(1 for c in checks if c.get("status") == "warning")
    failed   = sum(1 for c in checks if c.get("status") == "fail")

    score_color = "#16a34a" if score >= 80 else "#d97706" if score >= 60 else "#dc2626"
    grade = "Good Standing" if score >= 80 else "Needs Attention" if score >= 60 else "Action Required"

    report_link = f"{FRONTEND_URL}/report/{scan_id}"
    results_link = f"{FRONTEND_URL}/results/{scan_id}"
    upgrade_link = f"{FRONTEND_URL}/results/{scan_id}"

    # Build failed checks list for paid users
    failed_items_html = ""
    if paid and checks:
        fail_checks = [c for c in checks if c.get("status") == "fail"]
        if fail_checks:
            items = "".join(
                f"""<tr>
                  <td style="padding:10px 12px;border-bottom:1px solid #fee2e2;">
                    <strong style="color:#111827;font-size:14px;">{c.get('rule_name','')}</strong><br>
                    <span style="color:#6b7280;font-size:13px;">{c.get('message','')}</span>
                    {"<br><span style='color:#374151;font-size:13px;margin-top:4px;display:block;'><strong>Fix:</strong> " + c.get('remediation','') + "</span>" if c.get('remediation') else ""}
                  </td>
                </tr>"""
                for c in fail_checks[:10]
            )
            failed_items_html = f"""
            <h3 style="color:#dc2626;font-size:15px;margin:24px 0 8px;">Violations to fix ({len(fail_checks)})</h3>
            <table style="width:100%;border-collapse:collapse;background:#fef2f2;border-radius:8px;overflow:hidden;border:1px solid #fecaca;">
              {items}
            </table>"""

    paid_section = f"""
        <div style="text-align:center;margin:28px 0;">
          <a href="{report_link}" style="display:inline-block;background:#2563eb;color:#fff;font-weight:700;font-size:15px;padding:14px 28px;border-radius:10px;text-decoration:none;">
            Download PDF Report
          </a>
          <p style="color:#6b7280;font-size:12px;margin-top:8px;">Opens a print-ready report — use Ctrl+P / Cmd+P to save as PDF</p>
        </div>
        {failed_items_html}
    """ if paid else f"""
        <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:20px;text-align:center;margin:24px 0;">
          <p style="color:#92400e;font-size:14px;margin:0 0 14px;">
            <strong>{failed} violation{"s" if failed != 1 else ""}</strong> found — upgrade to see exactly what to fix.
          </p>
          <a href="{upgrade_link}" style="display:inline-block;background:#d97706;color:#fff;font-weight:700;font-size:14px;padding:12px 24px;border-radius:8px;text-decoration:none;">
            Get Fix Instructions — $19
          </a>
        </div>
    """

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 16px;">
    <tr><td>
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">

        <!-- Header -->
        <tr><td style="background:#1e3a5f;padding:28px 32px;">
          <p style="margin:0;color:#93c5fd;font-size:12px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;">complywithjudy.com</p>
          <h1 style="margin:6px 0 0;color:#fff;font-size:22px;font-weight:800;">Your Compliance Report</h1>
        </td></tr>

        <!-- Score -->
        <tr><td style="padding:28px 32px 0;">
          <p style="margin:0 0 4px;font-size:13px;color:#6b7280;">Scanned URL</p>
          <p style="margin:0 0 20px;font-size:15px;font-weight:600;color:#111827;word-break:break-all;">{url}</p>
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="text-align:center;background:#f9fafb;border-radius:12px;padding:20px;">
                <p style="margin:0;font-size:48px;font-weight:800;color:{score_color};line-height:1;">{score}</p>
                <p style="margin:4px 0 0;font-size:13px;color:#6b7280;">/100 &nbsp;·&nbsp; <strong style="color:{score_color};">{grade}</strong></p>
              </td>
            </tr>
          </table>

          <!-- Summary pills -->
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;">
            <tr>
              <td width="33%" style="text-align:center;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px;">
                <p style="margin:0;font-size:22px;font-weight:800;color:#16a34a;">{passed}</p>
                <p style="margin:2px 0 0;font-size:11px;font-weight:600;color:#16a34a;">Passed</p>
              </td>
              <td width="4%"></td>
              <td width="29%" style="text-align:center;background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px;">
                <p style="margin:0;font-size:22px;font-weight:800;color:#d97706;">{warnings}</p>
                <p style="margin:2px 0 0;font-size:11px;font-weight:600;color:#d97706;">Warnings</p>
              </td>
              <td width="4%"></td>
              <td width="30%" style="text-align:center;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px;">
                <p style="margin:0;font-size:22px;font-weight:800;color:#dc2626;">{failed}</p>
                <p style="margin:2px 0 0;font-size:11px;font-weight:600;color:#dc2626;">Failed</p>
              </td>
            </tr>
          </table>
        </td></tr>

        <!-- CTA / violations -->
        <tr><td style="padding:0 32px 28px;">
          {paid_section}
          <p style="margin:20px 0 0;text-align:center;">
            <a href="{results_link}" style="color:#2563eb;font-size:13px;">View full results online →</a>
          </p>
        </td></tr>

        <!-- Footer -->
        <tr><td style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;">
          <p style="margin:0;font-size:11px;color:#9ca3af;text-align:center;">
            complywithjudy.com &nbsp;·&nbsp; California RE/Lending Compliance
            <br>This is an automated report. Not legal advice.
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def handle_checkout(body: dict) -> dict:
    """
    Create a Stripe Checkout session and return the redirect URL.
    Body: { price_id, scan_id? }
    """
    price_id = body.get("price_id", "").strip()
    scan_id = body.get("scan_id", "").strip()

    if not price_id or price_id not in PRICE_TIER_MAP:
        return _response(400, {"error": "Invalid price_id"})

    frontend_base = os.environ.get("FRONTEND_URL", "https://complywithjudy.com")
    success_url = f"{frontend_base}/results/{scan_id}?payment=success" if scan_id else f"{frontend_base}/?payment=success"
    cancel_url = f"{frontend_base}/results/{scan_id}?payment=cancelled" if scan_id else f"{frontend_base}/"

    params = {
        "mode": "payment" if PRICE_TIER_MAP[price_id] in ("single", "fix_verify") else "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata[scan_id]": scan_id,
        "metadata[price_id]": price_id,
    }

    try:
        session = _stripe_request("POST", "/v1/checkout/sessions", params)
        return _response(200, {"url": session["url"], "session_id": session["id"]})
    except Exception as e:
        print(f"Checkout error: {e}")
        return _response(500, {"error": str(e)})


def handle_webhook(body_raw: str, stripe_signature: str) -> dict:
    """
    Verify Stripe webhook signature and process checkout.session.completed.
    Unlocks the paid tier on the associated scan in Supabase.
    """
    if not STRIPE_WEBHOOK_SECRET:
        return _response(400, {"error": "Webhook secret not configured"})

    # Verify signature
    try:
        parts = {p.split("=")[0]: p.split("=")[1] for p in stripe_signature.split(",")}
        timestamp = parts.get("t", "")
        sig = parts.get("v1", "")
        signed_payload = f"{timestamp}.{body_raw}"
        expected = hmac.new(
            STRIPE_WEBHOOK_SECRET.encode(),
            signed_payload.encode(),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return _response(400, {"error": "Invalid signature"})
    except Exception as e:
        return _response(400, {"error": f"Signature verification failed: {e}"})

    try:
        event = json.loads(body_raw)
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON"})

    if event.get("type") == "checkout.session.completed":
        session = event.get("data", {}).get("object", {})
        metadata = session.get("metadata", {})
        scan_id = metadata.get("scan_id", "").strip()
        price_id = metadata.get("price_id", "").strip()

        if scan_id and price_id in PRICE_TIER_MAP:
            tier = PRICE_TIER_MAP[price_id]
            try:
                supabase.table("scans").update({
                    "tier": tier,
                    "stripe_session_id": session.get("id"),
                    "paid_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", scan_id).execute()
                print(f"Scan {scan_id} unlocked as tier={tier}")

                # Send paid report email using customer email from Stripe or scan record
                customer_email = session.get("customer_details", {}).get("email", "")
                if not customer_email:
                    # Fall back to email stored on scan row
                    scan_row = supabase.table("scans").select("email,url,score,results").eq("id", scan_id).single().execute()
                    if scan_row.data:
                        customer_email = scan_row.data.get("email", "") or ""
                        scan_data = {
                            "id": scan_id,
                            "url": scan_row.data.get("url", ""),
                            "score": scan_row.data.get("score", 0),
                            "results": scan_row.data.get("results") or {},
                        }
                    else:
                        scan_data = {"id": scan_id, "url": "", "score": 0, "results": {}}
                else:
                    scan_row = supabase.table("scans").select("url,score,results").eq("id", scan_id).single().execute()
                    scan_data = {
                        "id": scan_id,
                        "url": scan_row.data.get("url", "") if scan_row.data else "",
                        "score": scan_row.data.get("score", 0) if scan_row.data else 0,
                        "results": scan_row.data.get("results") or {} if scan_row.data else {},
                    }

                if customer_email:
                    _send_email(
                        to=customer_email,
                        subject=f"Your full compliance report is ready — {scan_data['url']}",
                        html=_build_results_email(scan_data, paid=True)
                    )
                    print(f"Paid report email sent to {customer_email}")
            except Exception as e:
                print(f"Supabase update failed for scan {scan_id}: {e}")

    return _response(200, {"received": True})


def scrape_page(url: str) -> dict:
    """
    Launch headless Chromium, load the URL, return HTML + visible text.
    Handles common failure modes gracefully.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--no-zygote",
                "--disable-gpu",
                "--single-process",
            ]
        )
        page = browser.new_page(
            user_agent="Mozilla/5.0 (compatible; ComplianceBot/1.0; +https://complywithjudy.com)"
        )

        try:
            response = page.goto(url, wait_until="networkidle", timeout=30000)
            status_code = response.status if response else 0

            # Extra wait for JS-heavy SPAs (React, Vue, etc.) to finish rendering
            # footer/compliance disclosures that load after initial hydration
            page.wait_for_timeout(3500)

            # Pull full rendered HTML (includes JS-rendered content, JSON-LD, hidden elements)
            html = page.content()

            # Visible text from innerText
            inner_text = page.evaluate("() => document.body.innerText") or ""

            # Stripped innerHTML — catches alt text, aria-labels, hidden compliance
            # footers (display:none), and JSON-LD <script> blocks
            raw_html = page.evaluate("() => document.body.innerHTML") or ""
            import re as _re
            stripped_html = _re.sub(r"<[^>]+>", " ", raw_html)
            stripped_html = _re.sub(r"\s+", " ", stripped_html)

            # Head meta/JSON-LD (catches schema.org DRE/NMLS in structured data)
            head_html = page.evaluate(
                "() => document.head ? document.head.innerHTML : ''"
            ) or ""
            head_text = _re.sub(r"<[^>]+>", " ", head_html)

            text = f"{inner_text}\n{stripped_html}\n{head_text}"

            return {
                "success": True,
                "html": html,
                "text": text,
                "status_code": status_code,
                "final_url": page.url
            }

        except Exception as e:
            error_type = type(e).__name__
            # Classify the failure for the user
            if "Timeout" in error_type:
                message = "The page took too long to load. Try again or paste your HTML manually."
            elif "net::ERR_NAME_NOT_RESOLVED" in str(e):
                message = "Domain not found. Check the URL and try again."
            elif "net::ERR_CONNECTION_REFUSED" in str(e):
                message = "Site refused the connection. It may be down or blocking automated access."
            else:
                message = f"Could not access this page: {str(e)[:200]}"

            return {
                "success": False,
                "error": message,
                "error_type": error_type,
                "html": "",
                "text": ""
            }
        finally:
            browser.close()


def lambda_handler(event, context):
    """
    Main Lambda entry point — routes to scan, checkout, or webhook handler.
    """
    # CORS preflight
    method = event.get("httpMethod", event.get("requestContext", {}).get("http", {}).get("method", "POST"))
    if method == "OPTIONS":
        return _response(200, {})

    # Route by path
    path = event.get("path", event.get("rawPath", "/"))
    body_raw = event.get("body", "{}")
    if isinstance(body_raw, bytes):
        body_raw = body_raw.decode()
    if event.get("isBase64Encoded") and body_raw:
        body_raw = base64.b64decode(body_raw).decode()

    if path.rstrip("/").endswith("/checkout"):
        try:
            body = json.loads(body_raw) if body_raw else {}
        except json.JSONDecodeError:
            return _response(400, {"error": "Invalid JSON"})
        return handle_checkout(body)

    if path.rstrip("/").endswith("/webhook"):
        sig = event.get("headers", {}).get("stripe-signature", "")
        return handle_webhook(body_raw, sig)

    # Default: scan handler
    scan_id = None
    try:
        body = json.loads(body_raw) if isinstance(body_raw, str) else body_raw

        scan_id = body.get("scan_id")
        url = body.get("url", "").strip()
        raw_profession = body.get("profession", "realestate")
        # Normalize frontend values → DB/rule-engine values
        profession = {
            "real_estate": "realestate",
            "mortgage": "lending",
        }.get(raw_profession, raw_profession)
        email = body.get("email", "").strip() or None

        if not url:
            return _response(400, {"error": "Missing required field: url"})

        if scan_id:
            # Row already exists — mark as running, update email if provided
            update_data: dict = {"status": "running"}
            if email:
                update_data["email"] = email
            supabase.table("scans").update(update_data).eq("id", scan_id).execute()
        else:
            # Create a new scan row and return its ID
            import uuid as _uuid
            scan_id = str(_uuid.uuid4())
            insert_data: dict = {
                "id": scan_id,
                "url": url,
                "profession": profession,
                "status": "running",
                "tier": "free",
            }
            if email:
                insert_data["email"] = email
            supabase.table("scans").insert(insert_data).execute()

        # Scrape the page
        scrape_result = scrape_page(url)

        if not scrape_result["success"]:
            # Update scan as failed with user-friendly error
            supabase.table("scans").update({
                "status": "failed",
                "summary": {"error": scrape_result["error"], "error_type": scrape_result["error_type"]}
            }).eq("id", scan_id).execute()

            return _response(200, {
                "scan_id": scan_id,
                "status": "failed",
                "error": scrape_result["error"],
                "fallback_available": True  # tells frontend to offer manual paste
            })

        # Run compliance checks
        results = check_compliance(
            html=scrape_result["html"],
            text=scrape_result["text"],
            url=url,
            profession=profession
        )

        # Store results in Supabase
        supabase.table("scans").update({
            "status": "completed",
            "score": results["score"],
            "summary": results["summary"],
            "results": results,
            "completed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", scan_id).execute()

        # Send free summary email if email was captured
        if email:
            scan_data = {"id": scan_id, "url": url, "score": results["score"], "results": results}
            _send_email(
                to=email,
                subject=f"Your compliance score: {results['score']}/100 — {url}",
                html=_build_results_email(scan_data, paid=False)
            )

        return _response(200, {
            "scan_id": scan_id,
            "status": "completed",
            "score": results["score"],
            "summary": results["summary"]
        })

    except Exception as e:
        print(f"Lambda error: {traceback.format_exc()}")
        if scan_id:
            try:
                supabase.table("scans").update({
                    "status": "failed",
                    "summary": {"error": "Internal processing error. Please try again."}
                }).eq("id", scan_id).execute()
            except Exception:
                pass
        return _response(500, {"error": "Internal server error"})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "POST,OPTIONS"
        },
        "body": json.dumps(body)
    }
