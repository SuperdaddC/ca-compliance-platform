"""
AWS Lambda Handler — Compliance Platform
Receives a URL + profession, scrapes the page, runs the rule engine, stores results in Supabase.
"""

import json
import os
import traceback
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright
from supabase import create_client, Client
from rule_engine import check_compliance

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def scrape_page(url: str) -> dict:
    """
    Launch headless Chromium, load the URL, return HTML + visible text.
    Handles common failure modes gracefully.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
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
    Main Lambda entry point.

    Expected event body:
    {
        "scan_id": "uuid",          # Supabase scan record (already created by frontend)
        "url": "https://...",
        "profession": "realestate" | "lending"
    }
    """
    try:
        body = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event

        scan_id = body.get("scan_id")
        url = body.get("url", "").strip()
        profession = body.get("profession", "realestate")

        if not url or not scan_id:
            return _response(400, {"error": "Missing required fields: url, scan_id"})

        # Mark scan as running
        supabase.table("scans").update({
            "status": "running"
        }).eq("id", scan_id).execute()

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
