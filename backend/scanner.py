"""
scanner.py  —  ComplyWithJudy compliance engine
Replaces: lambda_function.py, lambda_handler.py, lambda_handler_full.py

Runs as a FastAPI server (Mac Mini / Railway / Render).
Deploy: uvicorn scanner:app --host 0.0.0.0 --port 8000
"""

import json
import re
import hashlib
import os
import time
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from dataclasses import dataclass, asdict

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scanner")

# ---------------------------------------------------------------------------
# Config  (set these as env vars — never hardcode secrets)
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "https://complywithjudy.com").split(",")

# API keys for external integrations (Judy/OpenClaw, future partners)
# Comma-separated list of valid keys. Set in .env: API_KEYS=key1,key2
API_KEYS = set(filter(None, os.environ.get("API_KEYS", "").split(",")))

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="ComplyWithJudy Scanner", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS + [
        "https://www.complywithjudy.com",
        "https://complywithjudy.netlify.app",
    ],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
    allow_credentials=True,
)

# ---------------------------------------------------------------------------
# Error classification  (fixes the bare "failed" dashboard problem)
# ---------------------------------------------------------------------------
class ScanErrorType(str, Enum):
    TIMEOUT        = "timeout"          # site took too long to respond
    BLOCKED        = "blocked"          # site returned 403/captcha
    DNS_FAIL       = "dns_fail"         # domain doesn't resolve
    SSL_ERROR      = "ssl_error"        # bad cert
    EMPTY_PAGE     = "empty_page"       # JS rendered nothing
    RATE_LIMITED   = "rate_limited"     # our IP was rate-limited
    INTERNAL       = "internal"         # bug in our code

ERROR_MESSAGES = {
    ScanErrorType.TIMEOUT:      "The website took too long to respond. It may be slow or blocking scanners. Try again or contact support.",
    ScanErrorType.BLOCKED:      "This website's firewall blocked our scan (usually Imperva, PerimeterX, or similar bot protection). No charge for blocked scans. You can use your scan credit on a different site, ask the site owner to whitelist our scanner, or contact us at support@complywithjudy.com for a manual review.",
    ScanErrorType.DNS_FAIL:     "The domain couldn't be found. Check that the URL is correct and the site is live.",
    ScanErrorType.SSL_ERROR:    "The website has an SSL/certificate error. This itself may be a compliance issue.",
    ScanErrorType.EMPTY_PAGE:   "The website loaded but appeared empty — it may require login or use unsupported rendering.",
    ScanErrorType.RATE_LIMITED: "Our scanner was temporarily rate-limited by the site. Please retry in a few minutes.",
    ScanErrorType.INTERNAL:     "An unexpected error occurred on our end. Please retry — if it persists, contact support.",
}

def classify_error(exc: Exception, stderr: str = "") -> ScanErrorType:
    msg = str(exc).lower() + stderr.lower()
    if "timeout" in msg or "timed out" in msg:
        return ScanErrorType.TIMEOUT
    if "net::err_name_not_resolved" in msg or "dns" in msg:
        return ScanErrorType.DNS_FAIL
    if "net::err_cert" in msg or "ssl" in msg or "certificate" in msg:
        return ScanErrorType.SSL_ERROR
    if "403" in msg or "captcha" in msg or "forbidden" in msg or "blocked" in msg or "human verification" in msg:
        return ScanErrorType.BLOCKED
    if "rate" in msg and "limit" in msg:
        return ScanErrorType.RATE_LIMITED
    return ScanErrorType.INTERNAL

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class ScanRequest(BaseModel):
    url: str
    profession: str          # "realestate" | "lending"
    email: str               # required — captures for remarketing
    scan_id: Optional[str] = None   # Supabase scan row ID (for status updates)
    user_id: Optional[str] = None
    courtesy_to: Optional[str] = None       # partner email — sends full report as courtesy
    courtesy_name: Optional[str] = None     # partner's name (for personalization)

class CheckResult(BaseModel):
    id: str
    name: str
    status: str              # "pass" | "fail" | "warn" | "skip"
    description: str
    detail: Optional[str] = None
    source_url: Optional[str] = None
    fix: Optional[str] = None       # only returned for paid tiers

# ---------------------------------------------------------------------------
# IP fingerprint helpers  (abuse prevention)
# ---------------------------------------------------------------------------
def make_fingerprint(ip: str, email: str) -> str:
    raw = f"{ip}:{email.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()

ADMIN_EMAILS = {"mike@thecolyerteam.com", "mjcolyer@gmail.com"}

# ---------------------------------------------------------------------------
# Review Queue config
# ---------------------------------------------------------------------------
# Rules that trigger auto-population into the review queue.
# These are the rule_ids where the scanner's result is ambiguous and needs human eyes.
# Matches rules that set screenshot_required=true in rule_engine.py (R02, R03, R05, R10, R15, R16, R18)
REVIEW_QUEUE_RULES = {
    "responsible_broker",      # R02 — ambiguous broker disclosure
    "safe_nmls",               # R03 — NMLS format edge cases
    # "ab723_images",          # R05 — removed: scanner can't verify if images are altered, just warns
    "physical_address",        # R10 — address regex false positives
    "equal_housing_lender",    # R15 — EHO vs EHL, image-only logos
    "dfpi_prohibited",         # R16 — borderline advertising claims
    "nmls_consumer_access",    # R18 — link presence ambiguity
    "equal_housing",           # RE equivalent of R15
}
# Future allowlist for rules that don't set screenshot_required but may need review
REVIEW_QUEUE_EXTRA_RULES: set = set()  # e.g., {"ccpa_privacy", "tila_apr"}

SCANNER_VERSION = "v1.2"


# ---------------------------------------------------------------------------
# Admin auth middleware
# ---------------------------------------------------------------------------
async def verify_admin(request: Request) -> str:
    """Extract JWT from Authorization header, verify admin role. Returns user_id or raises 403."""
    auth_header = request.headers.get("Authorization", "")
    # Also accept X-API-Key for admin access (batch scripts)
    api_key = request.headers.get("X-API-Key", "")
    if api_key and api_key in API_KEYS:
        return "api_key_admin"

    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Admin access required. Provide Authorization: Bearer <jwt> header.")
    token = auth_header.split(" ", 1)[1]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_SERVICE_KEY}
            )
            if r.status_code != 200:
                raise HTTPException(status_code=403, detail="Invalid or expired token")
            user = r.json()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/user_profiles",
                headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                params={"id": f"eq.{user['id']}", "select": "role"}
            )
            profiles = r.json()
        if not profiles or profiles[0].get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        return user["id"]
    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"Admin auth failed: {e}")
        raise HTTPException(status_code=403, detail="Admin authentication failed")


# ---------------------------------------------------------------------------
# Review Queue population
# ---------------------------------------------------------------------------
async def _populate_review_queue(
    scan_id: str,
    site_url: str,
    page_url: str,
    profession: str,
    entity_type: str,
    score: int,
    rule_results: list,
    screenshot_hex: str = "",
    platform: str = "unknown",
    dre_info: dict = None,
):
    """
    Auto-populate review_queue with ambiguous scanner results.
    Only inserts rules in REVIEW_QUEUE_RULES or REVIEW_QUEUE_EXTRA_RULES
    that have status 'fail' or 'warn'. Uses ON CONFLICT DO NOTHING for dedup.
    """
    review_rules = REVIEW_QUEUE_RULES | REVIEW_QUEUE_EXTRA_RULES
    items_to_insert = []

    for r in rule_results:
        if r.id not in review_rules:
            continue
        if r.status not in ("fail", "warn"):
            continue
        item = {
            "scan_id": scan_id,
            "site_url": site_url,
            "page_url": page_url,
            "profession": profession,
            "entity_type": entity_type,
            "score": score,
            "rule_id": r.id,
            "rule_name": r.name,
            "scanner_status": r.status,
            "scanner_detail": f"{r.description} {r.detail}".strip(),
            "scanner_evidence": r.detail[:500] if r.detail else "",
            "scanner_version": SCANNER_VERSION,
            "rule_version": SCANNER_VERSION,
            "review_status": "pending",
            "source": "auto",
        }
        # Auto-populate broker_info from DRE lookup for responsible_broker items
        if r.id == "responsible_broker" and dre_info and dre_info.get("responsible_broker_lic"):
            item["broker_info"] = {
                "name": dre_info.get("responsible_broker"),
                "dre": dre_info.get("responsible_broker_lic"),
                "brokerage": dre_info.get("responsible_broker"),
                "address": dre_info.get("responsible_broker_address"),
                "source": "dre_lookup",
            }
        items_to_insert.append(item)

    if not items_to_insert:
        return

    # Insert with ON CONFLICT DO NOTHING (dedup on site_url + rule_id for pending/claimed)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/review_queue",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=ignore-duplicates",
                },
                json=items_to_insert,
            )
            if r.status_code >= 300:
                # FK constraint on scan_id? Retry without scan_id
                if "scan_id" in r.text and ("23503" in r.text or "foreign key" in r.text.lower()):
                    log.warning(f"Review queue FK error on scan_id — retrying without scan_id")
                    for item in items_to_insert:
                        item["scan_id"] = None
                    r2 = await client.post(
                        f"{SUPABASE_URL}/rest/v1/review_queue",
                        headers={
                            "apikey": SUPABASE_SERVICE_KEY,
                            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                            "Content-Type": "application/json",
                            "Prefer": "resolution=ignore-duplicates",
                        },
                        json=items_to_insert,
                    )
                    if r2.status_code >= 300:
                        log.warning(f"Review queue insert failed (retry): {r2.status_code} {r2.text[:200]}")
                    else:
                        log.info(f"Review queue: inserted {len(items_to_insert)} items for {site_url} (no scan_id)")
                else:
                    log.warning(f"Review queue insert failed: {r.status_code} {r.text[:200]}")
            else:
                log.info(f"Review queue: inserted {len(items_to_insert)} items for {site_url}")
    except Exception as e:
        log.warning(f"Review queue population failed: {e}")

    # Upload screenshot to Supabase Storage if available
    if screenshot_hex and items_to_insert:
        try:
            screenshot_bytes = bytes.fromhex(screenshot_hex)
            # Use scan_id if available, otherwise generate a unique name
            file_id = scan_id if scan_id else str(uuid.uuid4())
            storage_path = f"{file_id}_page.jpg"
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{SUPABASE_URL}/storage/v1/object/review-assets/{storage_path}",
                    headers={
                        "apikey": SUPABASE_SERVICE_KEY,
                        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                        "Content-Type": "image/jpeg",
                        "x-upsert": "true",
                    },
                    content=screenshot_bytes,
                )
                if r.status_code < 300:
                    log.info(f"Screenshot uploaded: review-assets/{storage_path}")
                    # Link screenshot to all queue items for this site (pending ones just inserted)
                    async with httpx.AsyncClient(timeout=10) as client2:
                        r2 = await client2.get(
                            f"{SUPABASE_URL}/rest/v1/review_queue",
                            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                            params={
                                "site_url": f"eq.{site_url}",
                                "review_status": "eq.pending",
                                "select": "id",
                            },
                        )
                        if r2.status_code == 200:
                            queue_ids = [row["id"] for row in r2.json()]
                            asset_rows = [{
                                "review_item_id": qid,
                                "asset_type": "screenshot",
                                "storage_path": f"review-assets/{storage_path}",
                                "mime_type": "image/jpeg",
                                "caption": "Auto-captured page screenshot",
                            } for qid in queue_ids]
                            if asset_rows:
                                await client2.post(
                                    f"{SUPABASE_URL}/rest/v1/review_assets",
                                    headers={
                                        "apikey": SUPABASE_SERVICE_KEY,
                                        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                                        "Content-Type": "application/json",
                                        "Prefer": "return=minimal",
                                    },
                                    json=asset_rows,
                                )
                                log.info(f"Linked screenshot to {len(asset_rows)} queue items for {site_url}")
                else:
                    log.warning(f"Screenshot upload failed: {r.status_code} {r.text[:200]}")
        except Exception as e:
            log.warning(f"Screenshot upload failed: {e}")


async def check_scan_allowed(ip: str, email: str, user_id: Optional[str]) -> tuple[bool, str]:
    """
    Returns (allowed, reason).
    Rules:
      - Admin emails: always allowed
      - Paid users: unlimited
      - Free/anon: 1 scan per IP+email fingerprint ever
    """
    if email.lower().strip() in ADMIN_EMAILS:
        return True, "admin"

    if user_id:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/user_subscriptions",
                    headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                    params={"user_id": f"eq.{user_id}", "status": "eq.active", "select": "plan"}
                )
                subs = r.json()
                if isinstance(subs, list) and subs and subs[0].get("plan") in ("starter", "professional", "broker", "single"):
                    return True, "paid"
        except Exception as e:
            log.warning(f"Subscription check failed (table may not exist yet): {e}")

    # Free path: check fingerprint
    fp = make_fingerprint(ip, email)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/scan_fingerprints",
                headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                params={"fingerprint": f"eq.{fp}", "select": "id,used_at"}
            )
            existing = r.json()
            if isinstance(existing, list) and existing:
                return False, "You've already used your free scan. Create an account or upgrade to run more scans."
    except Exception as e:
        log.warning(f"Fingerprint check failed (table may not exist yet): {e}")

    return True, "free"

async def record_fingerprint(ip: str, email: str, scan_id: str):
    fp = make_fingerprint(ip, email)
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/scan_fingerprints",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal"
                },
                json={"fingerprint": fp, "email": email, "scan_id": scan_id, "used_at": datetime.now(timezone.utc).isoformat()}
            )
    except Exception as e:
        log.warning(f"Failed to record fingerprint (table may not exist yet): {e}")

# ---------------------------------------------------------------------------
# Supabase scan status helpers
# ---------------------------------------------------------------------------
async def update_scan_status(scan_id: str, status: str, result: dict = None, error_type: str = None, error_message: str = None):
    if not scan_id:
        return
    payload = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
    if result:
        payload["result"] = json.dumps(result)
        if "score" in result:
            payload["score"] = result["score"]
    if error_type:
        payload["error_type"] = error_type
    if error_message:
        payload["error_message"] = error_message

    try:
        async with httpx.AsyncClient() as client:
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/scans",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal"
                },
                params={"id": f"eq.{scan_id}"},
                json=payload
            )
    except Exception as e:
        log.warning(f"Failed to update scan status (table may not exist yet): {e}")


async def create_scan_record(scan_id: str, url: str, profession: str, email: str = None, user_id: str = None, is_free: bool = True):
    """INSERT a new scan row into Supabase so the results page can find it."""
    payload = {
        "id": scan_id,
        "url": url,
        "profession": profession,
        "status": "running",
        "is_free_scan": is_free,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if email:
        payload["email"] = email
    if user_id:
        payload["user_id"] = user_id

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/scans",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal"
                },
                json=payload
            )
            if r.status_code >= 300:
                log.warning(f"Failed to create scan record {scan_id}: {r.status_code} {r.text[:200]}")
            else:
                log.info(f"Created scan record {scan_id}")
    except Exception as e:
        log.warning(f"Failed to create scan record: {e}")


# ---------------------------------------------------------------------------
# Web scraper  (Playwright)
# ---------------------------------------------------------------------------
async def scrape_website(url: str) -> dict:
    """
    Returns dict with keys: text, html, head, screenshot_b64, page_count
    Raises classified exceptions on failure.
    """
    if not url.startswith("http"):
        url = f"https://{url}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        try:
            page = await context.new_page()

            # Navigate — use domcontentloaded instead of networkidle
            # (heavy RE sites with trackers/chat widgets never go fully idle)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeout:
                raise TimeoutError(f"timeout loading {url}")

            # Give JS-rendered content time to load after DOM ready
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeout:
                log.info(f"networkidle timeout for {url} — proceeding with what loaded")

            # Detect bot-challenge pages (Imperva, PerimeterX, DataDome, etc.)
            # These serve a JS challenge or checkbox before the real site loads.
            challenge_detected = False
            page_text_check = (await page.evaluate("() => document.body.innerText") or "").lower()
            challenge_signals = [
                "i am not a robot",
                "request rejected",
                "checking your browser",
                "verify you are human",
                "please verify",
                "just a moment",
                "access denied",
                "attention required",
                "enable javascript and cookies",
                "perimeterx",
                "datadome",
                "human verification",
            ]
            if any(sig in page_text_check for sig in challenge_signals) or len(page_text_check.strip()) < 200:
                challenge_detected = True
                log.info(f"Bot challenge detected for {url} — waiting for auto-resolve...")
                # Many challenges auto-solve after JS runs; wait and check again
                await page.wait_for_timeout(5000)
                page_text_check = (await page.evaluate("() => document.body.innerText") or "").lower()
                if any(sig in page_text_check for sig in challenge_signals) or len(page_text_check.strip()) < 200:
                    # Still blocked — try clicking the checkbox if it exists
                    try:
                        checkbox = await page.query_selector('iframe[src*="recaptcha"], iframe[title*="reCAPTCHA"], .g-recaptcha, #px-captcha, input[type="checkbox"]')
                        if checkbox:
                            await checkbox.click()
                            await page.wait_for_timeout(5000)
                    except Exception:
                        pass
                    # Final check
                    page_text_check = (await page.evaluate("() => document.body.innerText") or "").lower()
                    if any(sig in page_text_check[:500] for sig in challenge_signals[:4]) or len(page_text_check.strip()) < 200:
                        raise ValueError(f"blocked: bot protection on {url} requires human verification (likely Imperva/PerimeterX)")

            # Scroll to trigger lazy-loaded footer content (where disclosures often live)
            # Two-pass scroll: some frameworks (KW, Squarespace, IDX) load footer
            # content lazily and need a second scroll after initial content loads.
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            # Second scroll in case page height changed after lazy content loaded
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

            # Platform-specific extra wait for JS-heavy franchise sites
            current_url = page.url.lower()
            if any(p in current_url for p in ['.kw.com', 'kw.com/', 'compass.com', 'exprealty.com',
                                               'realtyonegroup.com', 'c21', 'century21',
                                               'bhhs.com', 'coldwellbanker', 'sothebysrealty']):
                log.info(f"Platform-specific extra wait for {current_url}")
                await page.wait_for_timeout(3000)
                # One more scroll after extra wait
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)

            inner_text = await page.evaluate("() => document.body.innerText") or ""
            raw_html   = await page.evaluate("() => document.body.innerHTML") or ""
            head_html  = await page.evaluate("() => document.head ? document.head.innerHTML : ''") or ""

            # Extract alt text, title, and aria-label from tags before stripping
            # (preserves "Equal Housing Opportunity" etc. from image alt attributes)
            attr_text_parts = re.findall(r'(?:alt|title|aria-label)\s*=\s*"([^"]*)"', raw_html, re.I)
            attr_text = " ".join(attr_text_parts)

            # Strip tags, collapse whitespace
            def strip(h): return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', h)).strip()

            stripped_html = strip(raw_html)
            head_text     = strip(head_html)
            combined      = f"{inner_text}\n{stripped_html}\n{head_text}\n{attr_text}".lower()

            if len(combined.strip()) < 100:
                raise ValueError("empty_page: page rendered with no content")

            # Screenshot for verification feature
            screenshot_bytes = await page.screenshot(type="jpeg", quality=70, full_page=False)
            screenshot_b64 = screenshot_bytes.hex()

            # Count pages linked (basic multi-page signal)
            links = await page.evaluate(
                "() => [...new Set([...document.querySelectorAll('a[href]')].map(a=>a.href).filter(h=>h.startsWith(window.location.origin)))].length"
            )

            # Detect EHO / Fair Housing logo via DOM (catches images, SVGs, iframes)
            # Tightened: word-bound "eho", visibility check, SVG nearby-text, SVG size heuristic
            eho_signals = await page.evaluate("""() => {
                const kwFull = /equal.?housing|fair.?housing|equal.?opportunity/i;
                const kwEho = /\\beho\\b/i;
                const kwLender = /lender/i;
                const test = (s) => kwFull.test(s) || kwEho.test(s);
                const isVisible = (el) => {
                    try {
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
                    } catch(e) { return true; }
                };
                const pageHeight = document.body.scrollHeight;
                const isInFooter = (el) => {
                    try {
                        const rect = el.getBoundingClientRect();
                        const scrollY = window.scrollY || window.pageYOffset;
                        return (rect.top + scrollY) > (pageHeight * 0.7);
                    } catch(e) { return false; }
                };
                const signals = [];
                const isRendered = (img) => {
                    // Check that the image actually has rendered dimensions (not just in DOM/JS template)
                    try {
                        const rect = img.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    } catch(e) { return false; }
                };

                // Check all img src, alt, and title
                document.querySelectorAll('img').forEach(img => {
                    if (!isVisible(img) || !isRendered(img)) return;
                    if (test(img.src) || test(img.alt || '') || test(img.title || ''))
                        signals.push('img:' + (img.alt || img.src).substring(0, 80));
                });

                // Check for small square-ish images in footer with no alt text
                // (common pattern for EHO logos with UUID filenames)
                document.querySelectorAll('footer img, [class*="footer"] img, [id*="footer"] img').forEach(img => {
                    if (!isVisible(img) || !isRendered(img)) return;
                    const w = img.naturalWidth || img.width;
                    const h = img.naturalHeight || img.height;
                    // EHO logos are typically 20-80px, roughly square or 2:1 ratio
                    if (w >= 15 && w <= 120 && h >= 15 && h <= 120) {
                        const src = (img.src || '').toLowerCase();
                        const alt = (img.alt || '').toLowerCase();
                        // Skip known non-EHO images
                        if (/logo|icon|social|facebook|twitter|instagram|linkedin|youtube|yelp|zillow|realtor/i.test(alt + src))
                            return;
                        // Already captured above? Skip
                        if (test(src) || test(alt)) return;
                        // This is a small footer image with no recognizable alt — potential EHO
                        signals.push('footer-img:' + w + 'x' + h + ':' + src.substring(src.lastIndexOf('/') + 1, src.lastIndexOf('/') + 40));
                    }
                });

                // Also check ALL small images in the bottom 30% of the page
                document.querySelectorAll('img').forEach(img => {
                    if (!isVisible(img) || !isRendered(img) || !isInFooter(img)) return;
                    const w = img.naturalWidth || img.width;
                    const h = img.naturalHeight || img.height;
                    if (w >= 15 && w <= 120 && h >= 15 && h <= 120) {
                        const src = (img.src || '').toLowerCase();
                        const alt = (img.alt || '').toLowerCase();
                        if (/logo|icon|social|facebook|twitter|instagram|linkedin|youtube|yelp|zillow|realtor/i.test(alt + src))
                            return;
                        if (test(src) || test(alt)) return;
                        // Check if nearby text contains EHO keywords
                        const parent = img.closest('a, div, span, li, p, footer, section');
                        if (parent) {
                            const nearby = (parent.textContent || '').trim();
                            if (kwFull.test(nearby)) {
                                const hasLender = kwLender.test(nearby);
                                signals.push('img-near-eho:' + (hasLender ? 'lender:' : '') + nearby.substring(0, 60));
                            }
                        }
                    }
                });

                // Check SVG — textContent, aria-label, AND nearby parent text
                document.querySelectorAll('svg').forEach(svg => {
                    if (!isVisible(svg)) return;
                    const t = (svg.textContent || '') + (svg.getAttribute('aria-label') || '');
                    if (test(t)) {
                        signals.push('svg:' + t.substring(0, 80));
                    } else {
                        const parent = svg.closest('a, div, span, li, footer, section');
                        if (parent) {
                            const nearby = parent.textContent || '';
                            if (kwFull.test(nearby))
                                signals.push('svg-nearby:' + nearby.substring(0, 80));
                        }
                    }
                });

                // Check aria-label on any element
                document.querySelectorAll('[aria-label]').forEach(el => {
                    if (!isVisible(el)) return;
                    if (test(el.getAttribute('aria-label')))
                        signals.push('aria:' + el.getAttribute('aria-label').substring(0, 80));
                });

                // Check CSS font-icon classes (e.g., ssi-eho, icon-eho, fa-house)
                const iconSelectors = [
                    '[class*="eho"]', '[class*="equal-housing"]', '[class*="fair-housing"]',
                    '[class*="equalhousing"]', '[class*="fairhousing"]',
                    'i.ssi-eho', 'i.ssi-realtor', 'span.eho', 'i.eho'
                ];
                for (const sel of iconSelectors) {
                    try {
                        document.querySelectorAll(sel).forEach(el => {
                            if (!isVisible(el)) return;
                            const cls = el.className || '';
                            // Only count if the class specifically references EHO, not just contains "eho" as substring
                            if (/\\beho\\b|equal.?housing|fair.?housing/i.test(cls)) {
                                const hasLender = kwLender.test(cls) || kwLender.test(el.textContent || '');
                                signals.push('icon:' + (hasLender ? 'lender:' : '') + cls.substring(0, 60));
                            }
                        });
                    } catch(e) {}
                }

                return signals;
            }""")
            log.info(f"EHO DOM signals for {url}: {eho_signals}")

            # Save the homepage URL before navigating away
            homepage_url = page.url

            # --- Follow privacy policy link for CCPA verification ---
            privacy_page_text = ""
            try:
                privacy_url = await page.evaluate("""() => {
                    const links = [...document.querySelectorAll('a[href]')];
                    const priv = links.find(a => {
                        const href = (a.href || '').toLowerCase();
                        const text = (a.textContent || '').toLowerCase();
                        // Must reference privacy, not external (google, facebook, etc.)
                        return (/privacy/i.test(href) || /privacy\s*policy/i.test(text))
                            && !/google\\.com|facebook\\.com|cloudflare\\.com|cookiebot\\.com|onetrust\\.com/i.test(href);
                    });
                    return priv ? priv.href : null;
                }""")
                if privacy_url and privacy_url != page.url:
                    log.info(f"Following privacy policy link: {privacy_url}")
                    await page.goto(privacy_url, wait_until="domcontentloaded", timeout=15000)
                    privacy_page_text = (await page.evaluate("() => document.body.innerText") or "").lower()
                    log.info(f"Privacy page loaded: {len(privacy_page_text)} chars")
            except Exception as e:
                log.warning(f"Failed to load privacy page: {e}")

            # --- Platform / web developer detection ---
            platform = _detect_platform(raw_html, head_html, homepage_url)
            log.info(f"Platform detected for {homepage_url}: {platform}")

            return {
                "text": combined,
                "raw_html": raw_html[:200000],  # cap at 200k chars
                "screenshot_hex": screenshot_b64,
                "internal_link_count": links,
                "url_final": homepage_url,
                "eho_signals": eho_signals,
                "privacy_page_text": privacy_page_text,
                "platform": platform,
            }

        finally:
            await browser.close()

# ---------------------------------------------------------------------------
# Compliance rule engine  (deterministic — no LLM needed for these)
# ---------------------------------------------------------------------------

# --- Shared patterns ---
DRE_LICENSE_RE = re.compile(
    r'\bdre\s*(?:no\.?)?\s*[#:.]?\s*\d{7,9}\b'  # "DRE #", "DRE No.", "CA DRE No. 00618471"
    r'|\bbre\s*(?:no\.?)?\s*[#:.]?\s*\d{7,9}\b' # legacy "BRE #" prefix (pre-2018 branding)
    r'|\bcalifornia\s+real\s+estate\s+broker\s*[#:.]?\s*\d{7,9}\b'
    r'|\bcalbre\s*[#:.]?\s*\d{7,9}\b'
    r'|\bca[\s\-]+dre\s*(?:no\.?)?\s*[#:.]?\s*\d{7,9}\b'  # "CA DRE No. 00618471", "CA-DRE #01234567"
    r'|\blicense\s+(?:id|#|number)\s*[#:.]?\s*0[12]\d{6}\b',  # "License ID: 01234567" (DRE format: starts with 0)
    re.I)
BROKER_DRE_RE  = re.compile(r'\b(broker|brokerage)\s*.{0,30}dre\s*[#:.]?\s*\d{7,9}\b', re.I)
NMLS_RE        = re.compile(r'\bnmls\s*[#:.]?\s*\d{4,10}\b', re.I)
EQUAL_HOUSING_RE = re.compile(r'equal\s+housing\s*(opportunity|lender|logo)?', re.I)
EHO_IMG_RE     = re.compile(
    r'(equal[_\-.\s]?housing'
    r'|\beho[_\-.](?:logo|icon|badge|seal)'    # "eho-logo", "eho_icon" etc. (require suffix)
    r'|\beho\.(?:png|svg|jpg|gif|webp)\b'       # "eho.png", "eho.svg" etc. (require extension)
    r'|fair[_\-.\s]?housing'
    r'|equal[_\-.\s]?opportunity)', re.I)
CCPA_RE        = re.compile(r'privacy\s+(policy|notice|statement|and\s+terms|&\s+terms)|ccpa|do\s+not\s+sell', re.I)
DO_NOT_SELL_RE = re.compile(r'do\s+not\s+sell(\s+or\s+share)?\s+(my|personal)', re.I)
ADA_RE         = re.compile(r'accessibility\s+(statement|policy|commitment|pledge|notice)|ada\s+complian|wcag|section\s+508|web\s+accessibility', re.I)
PHYSICAL_ADDR_RE = re.compile(
    r'\b\d{2,5}\s+[A-Z][a-z]+.*?'
    r'(ave|avenue|st|street|blvd|boulevard|dr|drive|rd|road|ln|lane|ct|court'
    r'|way|pkwy|parkway|pl|place|cir|circle|ste|suite|hwy|highway'
    r'|ter|terr|terrace|loop|sq|square|trail|trce|trace)\b', re.I)

# Placeholder / template emails that should not count as real contact info
PLACEHOLDER_EMAIL_RE = re.compile(
    r'\b(user@domain\.com|email@example\.com|name@company\.com|'
    r'your@email\.com|info@example\.com|test@test\.com|'
    r'example@example\.com|admin@example\.com|example@domain\.com|'
    r'youremail@email\.com|yourname@email\.com|'
    r'noreply@|no-reply@|donotreply@|'
    r'[a-z]+@sentry\.io|[a-z]+@placeholder\.)\b', re.I)

# Placeholder phone numbers (WordPress/template defaults)
PLACEHOLDER_PHONE_RE = re.compile(
    r'\(123\)\s*456[\-\s]?7890|\(000\)\s*000[\-\s]?0000|'
    r'555[\-\s]?555[\-\s]?5555|123[\-\s]?456[\-\s]?7890', re.I)


_EXTERNAL_PRIVACY_DOMAINS = re.compile(
    r'google\.com|gstatic\.com|googleapis\.com|facebook\.com|meta\.com|'
    r'twitter\.com|cloudflare\.com|cookiebot\.com|onetrust\.com', re.I)


def _has_own_privacy_link(html_str: str) -> bool:
    """Check for privacy links that belong to the site, not third-party widgets."""
    for m in re.finditer(r'href=["\']([^"\']*privacy[^"\']*)["\']', html_str, re.I):
        href = m.group(1)
        if not _EXTERNAL_PRIVACY_DOMAINS.search(href):
            return True
    return False


def _has_real_email(text: str, html: str) -> bool:
    """Check for a real email address, filtering out placeholders and template defaults."""
    # Find all emails in text
    emails_in_text = re.findall(r'[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}', text, re.I)
    real_text_emails = [e for e in emails_in_text if not PLACEHOLDER_EMAIL_RE.search(e)]
    if real_text_emails:
        return True
    # Check mailto: links in HTML
    mailto_emails = re.findall(r'mailto:([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,})', html, re.I)
    real_mailto = [e for e in mailto_emails if not PLACEHOLDER_EMAIL_RE.search(e)]
    if real_mailto:
        return True
    # Check for contact form / "email us" language
    if re.search(r'(click\s+(here\s+)?to\s+)?e[\-\s]?mail\s+(me|us)|contact\s+us|send\s+(a\s+)?message|get\s+in\s+touch|reach\s+(out|us)', text, re.I):
        return True
    if re.search(r'href=["\'][^"\']*(/contact|/email|/reach-out|/get-in-touch|/message)[^"\']*["\']', html, re.I):
        return True
    return False


# TILA
TILA_TRIGGER_RE = re.compile(
    r'(\d+\.?\d*\s*%\s*(interest|rate|fixed|variable|arm|apr)'
    r'|\$[\d,]+\.?\d*\s*(per\s+month|\/mo|monthly\s+payment)'
    r'|\d+\s*-?\s*year\s+(fixed|arm|loan|mortgage)'
    r'|\d+\.?\d*\s*%\s+(fixed|variable)\s+rate'
    r'|\d+\s*%\s+down\s*payment)',
    re.I
)
TILA_APR_RE = re.compile(r'\bapr\b', re.I)
TILA_WINDOW = 400

# ---------------------------------------------------------------------------
# DRE public lookup (async) — used for responsible broker identification
# ---------------------------------------------------------------------------
_DRE_INFO_CACHE: dict[str, dict] = {}  # persists across requests (names rarely change)


async def lookup_dre_info(license_number: str) -> dict:
    """
    Async DRE public lookup. Returns dict with keys:
      name, license_type, status, designated_officer, designated_officer_lic,
      salespersons (list), main_office, dba
    All values are strings or None. Results cached.
    """
    lic = re.sub(r'\D', '', license_number)
    if lic in _DRE_INFO_CACHE:
        return _DRE_INFO_CACHE[lic]

    info = {"name": None, "license_type": None, "status": None,
            "designated_officer": None, "designated_officer_lic": None,
            "responsible_broker": None, "responsible_broker_lic": None,
            "responsible_broker_address": None,
            "main_office": None, "dba": None}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://www2.dre.ca.gov/publicasp/pplinfo.asp?start=1",
                data={"h_nextstep": "SEARCH", "LICENSEE_NAME": "",
                      "CITY_STATE": "", "LICENSE_ID": lic},
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ComplianceBot/1.0; +https://complywithjudy.com)",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": "https://www2.dre.ca.gov/publicasp/pplinfo.asp",
                },
            )
            html_resp = r.text

        if "public information request complete" not in html_resp.lower():
            _DRE_INFO_CACHE[lic] = info
            return info

        def _extract(label):
            m = re.search(
                rf'<strong>{label}:</strong>.*?<FONT[^>]*>([^<\r\n]+)',
                html_resp, re.I | re.DOTALL)
            return re.sub(r'\s+', ' ', m.group(1)).strip() if m else None

        info["name"] = _extract("Name")
        info["license_type"] = _extract("License Type")
        info["status"] = _extract("License Status") or _extract("Status")
        info["main_office"] = _extract("Main Office")
        info["dba"] = _extract("DBA")

        # Extract designated officer (for corporation/broker licenses)
        do_match = re.search(
            r'DESIGNATED OFFICER.*?<A[^>]*>(\d{7,9})</A>\s*-\s*[^<]*?(\d{2}/\d{2}/\d{2,4})\s*<br\s*/?>([^<]+)',
            html_resp, re.I | re.DOTALL)
        if do_match:
            info["designated_officer_lic"] = do_match.group(1).strip()
            info["designated_officer"] = do_match.group(3).strip()
            # Clean up name format: "Sarkissian, Artin" -> "Artin Sarkissian"
            parts = info["designated_officer"].split(",", 1)
            if len(parts) == 2:
                info["designated_officer"] = f"{parts[1].strip()} {parts[0].strip()}"

        # Extract Responsible Broker (for salesperson licenses)
        rb_match = re.search(
            r'Responsible\s+Broker.*?License\s+ID:\s*<[aA][^>]*>\s*(\d{7,9})\s*</[aA]>(.*?)(?:Former\s+Responsible|Comment|Public\s+information)',
            html_resp, re.I | re.DOTALL)
        if rb_match:
            info["responsible_broker_lic"] = rb_match.group(1).strip()
            # Extract broker name — first non-empty line after the license link
            rb_block = rb_match.group(2)
            # Clean HTML tags and extract text lines
            rb_text = re.sub(r'<[^>]+>', '\n', rb_block)
            rb_lines = [l.strip() for l in rb_text.strip().split('\n') if l.strip()]
            if rb_lines:
                info["responsible_broker"] = rb_lines[0]  # e.g., "LPT Realty, Inc"
                # Remaining lines are the address
                if len(rb_lines) > 1:
                    info["responsible_broker_address"] = ', '.join(rb_lines[1:])

    except Exception as e:
        log.warning(f"DRE lookup failed for {lic}: {e}")

    _DRE_INFO_CACHE[lic] = info
    return info


# ---------------------------------------------------------------------------
# Platform / web developer detection
# ---------------------------------------------------------------------------
_PLATFORM_SIGNATURES = [
    # CMS / Builders
    ("wordpress", [r'/wp-content/', r'/wp-includes/', r'wp-json', r'<meta name="generator" content="WordPress']),
    ("wix", [r'wixstatic\.com', r'static\.parastorage\.com', r'X-Wix-', r'wix-warmup-data']),
    ("squarespace", [r'squarespace-cdn\.com', r'squarespace\.com/static', r'"siteId".*"squarespace"']),
    ("webflow", [r'assets\.website-files\.com', r'webflow\.com', r'data-wf-site']),
    ("godaddy_builder", [r'godaddy\.com/website-builder', r'img1\.wsimg\.com', r'secureservercdn\.net']),
    ("weebly", [r'weebly\.com', r'editmysite\.com']),
    ("shopify", [r'cdn\.shopify\.com', r'shopify\.com/s/']),

    # RE-specific platforms
    ("luxury_presence", [r'luxurypresence\.com', r'lp-cdn\.com', r'class="lp-']),
    ("sierra_interactive", [r'sierra\.com', r'sierrainteractive', r'idx\.sierra']),
    ("placester", [r'placester\.com', r'placester-cdn']),
    ("boomtown", [r'boomtownroi\.com', r'boomtown\.com']),
    ("chime", [r'chime\.me', r'chimecdn\.com']),
    ("kvcore", [r'kvcore\.com', r'kvcoreidx', r'insiderealestate\.com']),
    ("lofty", [r'lofty\.com', r'ylopo\.com', r'class="lofty-']),
    ("idx_broker", [r'idxbroker\.com', r'idx-broker', r'idxpress']),
    ("real_geeks", [r'realgeeks\.com', r'rg-cdn']),
    ("agent_fire", [r'agentfire\.com', r'flavor="developer_flavor"']),
    ("easy_agent_pro", [r'easyagentpro\.com', r'jeap-']),
    ("z57", [r'z57\.com', r'propertypulse']),

    # Lending platforms
    ("lenderhomepage", [r'lenderhomepage\.com', r'class="lhp-']),
    ("mortgage_iq", [r'mortgageiq\.com', r'mymortgage-online\.com']),
    ("homebot", [r'homebot\.ai']),
    ("total_expert", [r'totalexpert\.com', r'totalexpert\.net']),

    # Franchise / brokerage platforms
    ("kw_command", [r'\.kw\.com', r'kw\.com/', r'kellerwilliams']),
    ("compass_platform", [r'compass\.com/agents', r'compass\.com/homes']),
    ("exp_realty", [r'exprealty\.com', r'\.exp\.com']),
    ("c21_platform", [r'\.c21\.', r'century21\.com', r'sites\.c21\.homes']),
    ("coldwell_banker", [r'coldwellbanker\.com', r'\.cbhome\.com']),
    ("bhhs", [r'bhhs\.com', r'berkshirehathaway']),
    ("sothebys", [r'sothebysrealty\.com', r'sir\.com']),
    ("remax", [r'remax\.com', r'\.remax\.']),
    ("redfin", [r'redfin\.com']),
    ("realogy", [r'realogy\.com', r'anywhere\.re']),
    ("side_platform", [r'side\.com', r'sideinc\.com']),
    ("real_brokerage", [r'onereal\.com', r'joinreal\.com']),

    # Generic
    ("elementor", [r'elementor', r'class="elementor-']),
    ("divi", [r'class="et_pb_', r'et-boc', r'Divi']),
]


def _detect_platform(html: str, head_html: str, url: str) -> str:
    """Detect the website platform/builder from HTML signatures."""
    combined = (html[:100000] + " " + head_html + " " + url).lower()
    matches = []
    for platform_name, patterns in _PLATFORM_SIGNATURES:
        for pattern in patterns:
            if re.search(pattern, combined, re.I):
                matches.append(platform_name)
                break
    if not matches:
        return "unknown"
    # If multiple match (e.g., wordpress + elementor), join them
    # Prefer the more specific match (RE platform > generic CMS)
    if len(matches) == 1:
        return matches[0]
    # Filter out generic if specific exists
    generic = {"wordpress", "elementor", "divi", "shopify"}
    specific = [m for m in matches if m not in generic]
    if specific:
        return specific[0]
    return matches[0]


# ---------------------------------------------------------------------------
# Entity classification — detect non-brokerage entities to skip inapplicable checks
# ---------------------------------------------------------------------------
def classify_entity(text: str) -> str:
    """
    Classify the website entity type from page content.
    Returns one of: 'nonprofit', 'commercial_developer', 'property_manager',
    'commercial_lender', or 'standard' (apply normal checks).
    """
    lower = text.lower()

    # Nonprofit signals — require STRONG self-identification, not just mentioning "nonprofit"
    # Must describe THEMSELVES as nonprofit (not "loans for nonprofits" or "nonprofit clients")
    nonprofit_strong = re.search(
        r'501\s*\(\s*c\s*\)\s*\(?\s*3'           # explicit 501(c)(3)
        r'|we\s+are\s+a\s+non[\-\s]?profit'       # "we are a nonprofit"
        r'|\bis\s+a\s+non[\-\s]?profit'            # "[org] is a nonprofit"
        r'|\bour\s+non[\-\s]?profit'               # "our nonprofit"
        r'|\btax[\-\s]deductible\s+donation'        # tax-deductible donations (not just "tax deductible")
        r'|\bdonate\s+(?:now|today|here)\b'         # active donation solicitation
        r'|\bcommunity\s+development\s+financial\s+institution\b'  # explicit CDFI
        , lower)
    if nonprofit_strong:
        if not re.search(r'\bdre\b.*#?\s*\d{7,9}|nmls.*#?\s*\d{4,10}|\bbroker\b|\brealtor\b|\bagent\b', lower):
            return 'nonprofit'

    # Commercial RE developer/investor (not a brokerage)
    if re.search(r'multifamily|apartment.{0,30}(?:develop|invest)|commercial\s+real\s+estate\s+invest|private\s+equity.*real\s+estate|fund\s+(?:i|ii|iii|iv|v)\b', lower):
        if not re.search(r'\bdre\b|\bnmls\b|\bbroker\b|\bagent\b|\brealtor\b', lower):
            return 'commercial_developer'

    # Property manager only (no sales activity)
    if re.search(r'property\s+manag|residential\s+manag|tenant\s+(?:service|portal|login)|leasing\s+office|rent\s+(?:collection|payment)', lower):
        if not re.search(r'\bdre\b|\bbroker\b|\bagent\b|for\s+sale|\blisting\b', lower):
            return 'property_manager'

    # SBA/commercial-only lender (not residential mortgage)
    if re.search(r'sba\s+504|sba\s+7\s*\(\s*a\s*\)|small\s+business\s+loan|commercial\s+(?:loan|lending|finance)', lower):
        if not re.search(r'mortgage|home\s+loan|residential\s+(?:loan|lending|mortgage)', lower):
            return 'commercial_lender'

    return 'standard'


# DRE-specific
RESPONSIBLE_BROKER_RE = re.compile(
    r'(responsible\s+broker|supervising\s+broker|broker\s+of\s+record|dba\s+.{0,60}broker'
    r'|is\s+a\s+real\s+estate\s+broker\s+licensed'
    r'|real\s+estate\s+broker.{0,30}license\s+number\s*[#:.]?\s*\d{7,9}'
    r'|brokerage.{0,30}license\s*[#:.]?\s*\d{7,9})', re.I
)
TEAM_NAME_RE = re.compile(
    r'\b(team|group|associates|partners|realty|properties)\b', re.I
)
AB723_RE = re.compile(
    r'(virtually\s+staged|digitally\s+enhanced|photo.*altered|ai.generated|image.*modified)', re.I
)
VIRTUAL_OFFICE_RE = re.compile(r'virtual\s+office|voa\b', re.I)
MLS_ATTR_RE = re.compile(r'(mls|multiple\s+listing|idx|internet\s+data\s+exchange)', re.I)


@dataclass
class RuleResult:
    id: str
    name: str
    status: str   # pass | fail | warn | skip
    description: str
    detail: str = ""
    source_url: str = ""
    fix: str = ""
    regulation: str = ""         # exact regulation text
    webmaster_email: str = ""    # pre-written email to send to webmaster


# ---------------------------------------------------------------------------
# Webmaster email templates
# ---------------------------------------------------------------------------
def _webmaster_email(subject: str, body: str) -> str:
    """Return a mailto-ready email template."""
    return f"Subject: {subject}\n\n{body}\n\n---\nThis issue was identified by ComplyWithJudy.com, a California real estate compliance scanner.\nLearn more at https://complywithjudy.com"


WM_DRE_LICENSE = _webmaster_email(
    "URGENT: DRE License Number Missing from Website",
    "Hi,\n\nOur website is missing the required California DRE license number. Under California Business & Professions Code §10140.6 and Commissioner's Regulation §2773, all real estate advertising must include the responsible licensee's DRE license number.\n\nPlease add the following to the site footer so it appears on every page:\n\nDRE #[YOUR_NUMBER]\n\nThis is a legal requirement — failure to comply can result in DRE disciplinary action.\n\nPlease confirm when this has been updated."
)

WM_RESPONSIBLE_BROKER = _webmaster_email(
    "URGENT: Responsible Broker Disclosure Missing",
    "Hi,\n\nOur website is missing the required responsible broker disclosure. Under California Business & Professions Code §10159.5 and DRE Commissioner's Regulation §2773, all advertising by a salesperson must include the supervising broker's identity.\n\nPlease add to the site footer:\n\n[Agent Name], DRE #[AGENT_NUMBER]\n[Brokerage Name], DRE #[BROKER_NUMBER]\nResponsible Broker: [Broker Name]\n\nPlease confirm when this has been updated."
)

WM_EQUAL_HOUSING = _webmaster_email(
    "Required: Equal Housing Opportunity Logo/Statement",
    "Hi,\n\nOur website needs the Equal Housing Opportunity logo and/or statement. Under the Fair Housing Act (42 U.S.C. §3604) and HUD advertising guidelines (24 CFR Part 109), all real estate advertising must include this.\n\nPlease add the Equal Housing Opportunity logo to the site footer. You can download the official logo from HUD's website.\n\nPlease confirm when this has been updated."
)

WM_CCPA = _webmaster_email(
    "Required: Privacy Policy Missing from Website",
    "Hi,\n\nOur website is missing a privacy policy. Under the California Consumer Privacy Act (Civil Code §1798.100-199) and its 2023 CPRA amendments, any business collecting personal information from California residents must post a compliant privacy policy.\n\nPlease add a Privacy Policy page that includes:\n- Categories of personal information collected\n- How personal information is used\n- Consumer rights under CCPA/CPRA\n- Contact information for privacy requests\n\nLink it in the site footer. Please confirm when this has been updated."
)

WM_AB723 = _webmaster_email(
    "Required: AB 723 Digitally Altered Image Disclosure",
    "Hi,\n\nIf any property images on our website are virtually staged, digitally enhanced, or AI-generated, California AB 723 (Civil Code §1102.6e) requires a disclosure near those images.\n\nPlease add the following text near any altered property photos:\n'Photo(s) may be virtually staged or digitally enhanced.'\n\nPlease confirm when this has been updated."
)

WM_NMLS = _webmaster_email(
    "URGENT: NMLS Number Missing from Website",
    "Hi,\n\nOur website is missing the required NMLS identification number. Under the SAFE Act (12 U.S.C. §5102-5116) and NMLS Policy Guidebook §5.1, all mortgage advertising must include the individual MLO's NMLS ID and the company NMLS ID.\n\nPlease add to the site footer:\n\nNMLS #[INDIVIDUAL_ID]\n[Company Name] NMLS #[COMPANY_ID]\n\nPlease confirm when this has been updated."
)

WM_TILA = _webmaster_email(
    "URGENT: TILA/Reg Z APR Disclosure Violation",
    "Hi,\n\nOur website mentions specific interest rates, monthly payments, or loan terms without the required APR disclosure nearby. Under the Truth in Lending Act (15 U.S.C. §1601) and Regulation Z (12 CFR §1026.24), any 'triggering term' (specific rate, payment amount, or loan term) requires all related APR and loan terms to be disclosed in close proximity.\n\nPlease either:\n1. Add the APR immediately adjacent to any rate or payment mentioned, OR\n2. Remove the specific rate/payment figures\n\nPlease confirm when this has been updated."
)

WM_EHL = _webmaster_email(
    "Required: Equal Housing Lender Statement Missing",
    "Hi,\n\nOur website is missing the required 'Equal Housing Lender' statement. Under Regulation B (12 CFR §1002.4) implementing the Equal Credit Opportunity Act, all mortgage lender advertising must include this statement and/or the Equal Housing Lender logo.\n\nPlease add 'Equal Housing Lender' and the logo to the site footer.\n\nPlease confirm when this has been updated."
)


# ---------------------------------------------------------------------------
# TILA / Reg Z check
# ---------------------------------------------------------------------------
def check_tila_proximity(text: str) -> RuleResult:
    """APR must appear within 400 chars of any TILA triggering term."""
    triggers = list(TILA_TRIGGER_RE.finditer(text))
    if not triggers:
        return RuleResult(
            id="tila_apr", name="TILA/Reg Z — APR Proximity",
            status="skip",
            description="No TILA triggering terms (rates, payments, or loan terms) found on page.",
            source_url="https://www.consumerfinance.gov/rules-policy/regulations/1026/24/",
            regulation="12 CFR §1026.24(d) — If an advertisement for credit states a rate of finance charge, it shall state the rate as an 'annual percentage rate,' using that term. If the annual percentage rate may be increased after consummation, the advertisement shall state that fact."
        )
    for m in triggers:
        start = max(0, m.start() - TILA_WINDOW)
        end   = min(len(text), m.end() + TILA_WINDOW)
        window = text[start:end]
        if TILA_APR_RE.search(window):
            return RuleResult(
                id="tila_apr", name="TILA/Reg Z — APR Proximity",
                status="pass",
                description="APR disclosure found near triggering term — compliant with Regulation Z.",
                source_url="https://www.consumerfinance.gov/rules-policy/regulations/1026/24/",
                regulation="12 CFR §1026.24(d) — Triggering terms require full APR disclosure in close proximity."
            )
    return RuleResult(
        id="tila_apr", name="TILA/Reg Z — APR Proximity",
        status="fail",
        description="Triggering rate/payment terms found but APR not disclosed nearby.",
        detail=f"Found triggering term: '{triggers[0].group()[:60]}' — no APR within {TILA_WINDOW} characters.",
        source_url="https://www.consumerfinance.gov/rules-policy/regulations/1026/24/",
        regulation="12 CFR §1026.24(d) — 'If an advertisement for credit states a rate of finance charge, it shall state the rate as an annual percentage rate, using that term.' Additional triggering terms include: the amount or percentage of any downpayment, the number of payments or period of repayment, or the amount of any payment. When any triggering term is used, the advertisement must also state: (i) the amount or percentage of the downpayment, (ii) the terms of repayment, and (iii) the annual percentage rate.",
        fix="Add the Annual Percentage Rate (APR) immediately adjacent to any specific interest rate, monthly payment, or loan term mentioned on the page. The APR must be displayed at least as prominently as the triggering term. If the rate is variable, state 'APR is variable and may increase after consummation.'",
        webmaster_email=WM_TILA
    )


# ---------------------------------------------------------------------------
# Real Estate checks
# ---------------------------------------------------------------------------
def run_realestate_checks(text: str, html: str, eho_signals: list = None,
                          dre_number: str = None, dre_info: dict = None,
                          privacy_page_text: str = "") -> list[RuleResult]:
    results = []

    # 1. DRE license number
    if DRE_LICENSE_RE.search(text):
        results.append(RuleResult("dre_license", "DRE License Number Display", "pass",
            "DRE license number found on page.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="California Business & Professions Code §10140.6 — 'A real estate licensee shall disclose his or her license identification number on all solicitation materials intended to be the first point of contact with consumers.' Commissioner's Regulation §2773 further requires the eight-digit license number on all advertising."))
    else:
        results.append(RuleResult("dre_license", "DRE License Number Display", "fail",
            "No DRE license number detected on this page.",
            detail="California law requires your DRE license number (format: DRE #01234567) to appear on every page of advertising material, including your website.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="California Business & Professions Code §10140.6 — 'A real estate licensee shall disclose his or her license identification number on all solicitation materials intended to be the first point of contact with consumers and on all real property purchase agreements when acting as an agent.' Commissioner's Regulation §2773 requires the eight-digit DRE license number to appear on all advertising, including internet advertising.",
            fix="Add your DRE license number to your website footer so it appears on every page. Use the format: 'DRE #01234567' or 'CalBRE #01234567'. The number must be your full 8-digit license number, not abbreviated.",
            webmaster_email=WM_DRE_LICENSE))

    # 2. Responsible broker disclosure
    #    Three-tier detection:
    #    a) Explicit text: "responsible broker", "supervising broker", etc.
    #    b) DRE lookup: if DRE number found, check if it's a broker/corporation license
    #       (the DRE number IS the broker identity per B&P §10140.6)
    #    c) DRE lookup reveals designated officer for corporation licenses
    has_broker_text = bool(RESPONSIBLE_BROKER_RE.search(text) or BROKER_DRE_RE.search(text))
    dre_is_broker = False
    dre_broker_name = None
    dre_designated_officer = None

    if dre_info and dre_info.get("name"):
        lic_type = (dre_info.get("license_type") or "").upper()
        # Corporation or Broker licenses = this IS the broker
        if any(t in lic_type for t in ("CORPORATION", "BROKER")):
            dre_is_broker = True
            dre_broker_name = dre_info["name"]
            if dre_info.get("designated_officer"):
                dre_designated_officer = dre_info["designated_officer"]

    if has_broker_text:
        results.append(RuleResult("responsible_broker", "Responsible Broker Disclosure", "pass",
            "Responsible broker disclosure found.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="California Business & Professions Code §10159.5 — Commissioner's Regulation §2773.1 — All advertising by or on behalf of a salesperson must include the identity of the responsible broker."))
    elif dre_is_broker:
        # DRE number belongs to a broker/corporation — they ARE the broker
        officer_note = f" Designated officer: {dre_designated_officer}." if dre_designated_officer else ""
        results.append(RuleResult("responsible_broker", "Responsible Broker Disclosure", "pass",
            f"Broker identified via DRE lookup: {dre_broker_name} ({(dre_info.get('license_type') or '').title()}).{officer_note}",
            detail=f"DRE public records confirm license #{dre_number} belongs to {dre_broker_name}. This is a broker-level license — the licensee IS the responsible broker.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="California Business & Professions Code §10140.6(b)(1) — The DRE license number identifies the responsible broker. For broker-owned sites, displaying the DRE number satisfies the broker disclosure requirement."))
    elif dre_number and dre_info and dre_info.get("name"):
        # DRE number found but it's a salesperson license — need broker info
        rb_name = dre_info.get("responsible_broker") or "Unknown"
        rb_lic = dre_info.get("responsible_broker_lic") or ""
        rb_addr = dre_info.get("responsible_broker_address") or ""
        rb_detail = f"Salesperson websites must identify the responsible/supervising broker by name and DRE license number."
        if rb_lic:
            rb_detail += f"\n\nDRE records show responsible broker: {rb_name}, DRE #{rb_lic}"
            if rb_addr:
                rb_detail += f", {rb_addr}"
        results.append(RuleResult("responsible_broker", "Responsible Broker Disclosure", "warn",
            f"DRE #{dre_number} is a salesperson license ({dre_info['name']}). Supervising broker not identified on page.",
            detail=rb_detail,
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="Commissioner's Regulation §2773.1 — 'The name of the broker must appear in advertising in a manner that is at least as prominent as the name of the salesperson.'",
            fix=f"Add your supervising broker's name and DRE number to your footer: '{rb_name}, DRE #{rb_lic}'." if rb_lic else "Add your supervising broker's name and DRE number to your footer."))
    else:
        results.append(RuleResult("responsible_broker", "Responsible Broker Disclosure", "fail",
            "No responsible or supervising broker identified on this page.",
            detail="Your website must identify the supervising/responsible broker by name and DRE license number.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="California Business & Professions Code §10159.5 — Salesperson advertising must include broker identity. Commissioner's Regulation §2773.1 — 'The name of the broker must appear in advertising in a manner that is at least as prominent as the name of the salesperson.'",
            fix="Add to your site footer: '[Your Name], DRE #[Your Number] | [Brokerage Name], DRE #[Broker Number]'.",
            webmaster_email=WM_RESPONSIBLE_BROKER))

    # 3. Equal Housing Opportunity
    #    Three-tier detection: text regex, HTML img regex, browser DOM signals
    #    DOM signals include: img:, svg:, aria:, svg-nearby:, img-near-eho:, footer-img:
    has_text = bool(EQUAL_HOUSING_RE.search(text))
    has_img  = bool(EHO_IMG_RE.search(html))
    strong_dom = [s for s in (eho_signals or [])
                  if s.startswith(('img:', 'svg:', 'aria:', 'svg-nearby:', 'img-near-eho:', 'icon:'))]
    has_dom = bool(strong_dom)
    if has_text or has_img or has_dom:
        evidence = ""
        if strong_dom:
            evidence = f"DOM signal: {strong_dom[0][:60]}"
        results.append(RuleResult("equal_housing", "Equal Housing Opportunity", "pass",
            "Equal Housing Opportunity logo or statement found.",
            detail=evidence,
            source_url="https://www.hud.gov/program_offices/fair_housing_equal_opp/advertising_and_marketing",
            regulation="Fair Housing Act (42 U.S.C. §3604(c)) — HUD Advertising Guidelines (24 CFR Part 109) require the Equal Housing Opportunity logo or statement in all real estate advertising."))
    else:
        results.append(RuleResult("equal_housing", "Equal Housing Opportunity", "fail",
            "Equal Housing Opportunity logo or statement not found.",
            detail="The Fair Housing Act and HUD advertising guidelines require all real estate advertising to include the Equal Housing Opportunity logo and/or the statement 'Equal Housing Opportunity.'",
            source_url="https://www.hud.gov/program_offices/fair_housing_equal_opp/advertising_and_marketing",
            regulation="Fair Housing Act (42 U.S.C. §3604(c)) — Prohibits discriminatory advertising. HUD Advertising Guidelines (24 CFR Part 109.30) — 'All advertising of residential real estate for sale, rent, or financing should contain an equal housing opportunity logotype, statement, or slogan.'",
            fix="Add the Equal Housing Opportunity logo and the words 'Equal Housing Opportunity' to your website footer. The logo should be clearly visible — not hidden or miniaturized.",
            webmaster_email=WM_EQUAL_HOUSING))

    # 4. Team advertising compliance
    if TEAM_NAME_RE.search(text):
        has_affiliation = bool(re.search(r'(keller williams|compass|coldwell|century 21|sotheby|exp realty|berkshire|remax|re/max|better homes|@properties|eXp|lyon|bhhs)', text, re.I))
        if not has_affiliation and not RESPONSIBLE_BROKER_RE.search(text) and not BROKER_DRE_RE.search(text):
            results.append(RuleResult("team_advertising", "Team Advertising Compliance", "warn",
                "Team name detected but broker affiliation may not be sufficiently prominent.",
                detail="The DRE requires that when a team advertises, the responsible broker's name must be displayed at least as prominently as the team name.",
                source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
                regulation="Commissioner's Regulation §2773.1 — 'The name of the broker shall appear in a manner that is at least as prominent as the name of the team or group in any advertising.' DRE RE 17 (Winter 2011/12) — Team names cannot imply the team is a separate licensed entity.",
                fix="Ensure your broker's name and DRE license number are displayed at least as prominently as your team name. The broker name must appear in the same font size or larger, not buried in fine print."))
        else:
            results.append(RuleResult("team_advertising", "Team Advertising Compliance", "pass",
                "Team name with apparent broker affiliation found.",
                source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
                regulation="Commissioner's Regulation §2773.1 — Broker identity must be at least as prominent as team name in advertising."))
    else:
        results.append(RuleResult("team_advertising", "Team Advertising Compliance", "skip",
            "No team name detected on this page — check not applicable."))

    # 5. AB 723 — digitally altered image disclosure
    has_photos = bool(re.search(r'<img ', html, re.I))
    if not has_photos:
        results.append(RuleResult("ab723_images", "AB 723 — Altered Image Disclosure", "skip",
            "No images detected on this page.",
            source_url="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202320240AB723"))
    elif AB723_RE.search(text):
        results.append(RuleResult("ab723_images", "AB 723 — Altered Image Disclosure", "pass",
            "Digitally altered image disclosure language found.",
            source_url="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202320240AB723",
            regulation="California AB 723 (2024), adding Civil Code §1102.6e — Requires disclosure when listing photographs have been 'digitally staged' or 'virtually staged or digitally altered or enhanced.' The disclosure must be 'in a conspicuous manner' near the affected image."))
    else:
        results.append(RuleResult("ab723_images", "AB 723 — Altered Image Disclosure", "warn",
            "Property images found but no AB 723 disclosure language detected.",
            detail="If any property images on this site are virtually staged, digitally enhanced, or AI-generated, California AB 723 requires a conspicuous disclosure.",
            source_url="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202320240AB723",
            regulation="California AB 723 (effective July 1, 2024), Civil Code §1102.6e — 'A listing that includes a photograph that has been digitally staged shall include a disclosure... that the photograph has been digitally staged.' Applies to virtual staging, AI-generated imagery, and significant digital alteration of property photos.",
            fix="If any property photos are virtually staged or digitally altered, add this disclosure near those images: 'Photo(s) may be virtually staged or digitally enhanced.' The disclosure must be conspicuous — not hidden in fine print or buried at the bottom of the page.",
            webmaster_email=WM_AB723))

    # 6. Virtual office advertisement rules
    if VIRTUAL_OFFICE_RE.search(text):
        if DRE_LICENSE_RE.search(text):
            results.append(RuleResult("virtual_office", "Virtual Office Advertisement (VOA) Rules", "pass",
                "Virtual office with DRE license — appears compliant.",
                source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
                regulation="Commissioner's Regulation §2770.1 — Virtual Office Advertisements (VOAs) must comply with the same advertising rules as traditional advertising, including display of the responsible broker's name and license number."))
        else:
            results.append(RuleResult("virtual_office", "Virtual Office Advertisement (VOA) Rules", "fail",
                "Virtual office reference found without DRE license number.",
                detail="All virtual office websites must identify the responsible broker and display DRE license numbers, just like physical office advertising.",
                source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
                regulation="Commissioner's Regulation §2770.1 — VOA operators must display the responsible broker's name, license number, and comply with all other advertising regulations under §2773.",
                fix="Add your DRE license number and broker information to the virtual office page."))

    # 7. CCPA privacy policy (check both text and HTML for links/anchors)
    #    Filter out external privacy links (Google reCAPTCHA, third-party widgets)
    #    Also check privacy_page_text if the subpage was followed
    all_privacy_text = text + " " + privacy_page_text
    has_privacy = CCPA_RE.search(text) or \
                  bool(re.search(r'(href|link|url).*?privacy[\-_\s]?policy|privacy[\-_\s]?policy.*?(href|link|url)', html, re.I)) or \
                  _has_own_privacy_link(html)
    has_dns     = DO_NOT_SELL_RE.search(all_privacy_text) or \
                  bool(re.search(r'do[\-_\s]*not[\-_\s]*sell', html, re.I))
    # Check privacy subpage for CCPA-specific content
    has_ccpa_on_subpage = bool(re.search(
        r'california\s+consumer\s+privacy|ccpa|cpra|right\s+to\s+(?:know|delete|opt)|california\s+privacy\s+rights',
        privacy_page_text, re.I)) if privacy_page_text else False
    if has_ccpa_on_subpage:
        has_dns = has_dns or bool(re.search(r'do\s+not\s+sell|opt[\-\s]out', privacy_page_text, re.I))
    if has_privacy:
        if has_dns:
            results.append(RuleResult("ccpa_privacy", "CCPA/CPRA Privacy Policy", "pass",
                "Privacy policy and 'Do Not Sell' language found.",
                source_url="https://oag.ca.gov/privacy/ccpa",
                regulation="California Consumer Privacy Act (Civil Code §1798.100-199), amended by CPRA (2023) — Businesses collecting personal information from California residents must provide a privacy policy disclosing categories of information collected, purposes, and consumer rights. §1798.120 requires a 'Do Not Sell or Share My Personal Information' link."))
        else:
            results.append(RuleResult("ccpa_privacy", "CCPA/CPRA Privacy Policy", "warn",
                "Privacy policy found, but no 'Do Not Sell or Share' link detected.",
                detail="If you sell or share personal information, CCPA/CPRA requires a conspicuous 'Do Not Sell or Share My Personal Information' link.",
                source_url="https://oag.ca.gov/privacy/ccpa",
                regulation="California Civil Code §1798.120(a) — 'A consumer shall have the right, at any time, to direct a business that sells or shares personal information about the consumer to third parties not to sell or share the consumer's personal information.' §1798.135 requires a 'clear and conspicuous link' titled 'Do Not Sell or Share My Personal Information.'",
                fix="Add a 'Do Not Sell or Share My Personal Information' link to your footer if you share any user data with third parties (including analytics, advertising, or lead generation services). If you use Google Analytics, Facebook Pixel, or similar tools, you likely need this link."))
    else:
        results.append(RuleResult("ccpa_privacy", "CCPA/CPRA Privacy Policy", "fail",
            "No privacy policy detected on this page.",
            detail="Any website collecting personal information (contact forms, email signups, cookies) from California residents must have a CCPA-compliant privacy policy.",
            source_url="https://oag.ca.gov/privacy/ccpa",
            regulation="California Consumer Privacy Act (Civil Code §1798.100) — 'A business that collects a consumer's personal information shall, at or before the point of collection, inform consumers as to the categories of personal information to be collected and the purposes for which the categories of personal information are collected or used.' Failure to post a privacy policy can result in fines of $2,500 per unintentional violation or $7,500 per intentional violation.",
            fix="Create a Privacy Policy page and link it in your website footer. The policy must include: (1) categories of personal information collected, (2) how it's used, (3) whether it's sold or shared, (4) consumer rights under CCPA/CPRA, and (5) contact information for privacy requests. Consider using a CCPA-compliant template from your legal provider.",
            webmaster_email=WM_CCPA))

    # 8. Contact information
    has_phone = bool(re.search(r'\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}', text)) and not PLACEHOLDER_PHONE_RE.search(text)
    has_email = _has_real_email(text, html)
    has_addr  = bool(PHYSICAL_ADDR_RE.search(text))
    if has_phone and has_email:
        results.append(RuleResult("contact_info", "Contact Information", "pass",
            "Phone and email contact found on page.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="DRE Advertising Guidelines and Business & Professions Code §10159.5 — The public should be able to identify and contact the responsible licensee from any advertising."))
    elif has_phone or has_email:
        results.append(RuleResult("contact_info", "Contact Information", "warn",
            f"{'Phone number' if has_phone else 'Email address'} found, but {'email' if has_phone else 'phone number'} not detected.",
            fix="Add both a phone number and email address to your website for consumer accessibility.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="DRE best practices recommend multiple contact methods be available."))
    else:
        results.append(RuleResult("contact_info", "Contact Information", "fail",
            "No phone number or email address detected on this page.",
            fix="Add your phone number and email address to the website, ideally in the header or footer so they appear on every page.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="DRE Advertising Guidelines — Consumers must be able to identify and contact the responsible licensee. Business & Professions Code §10159.5 requires advertised contact information to lead to the responsible broker."))

    # 9. Physical / mailing address
    if has_addr:
        results.append(RuleResult("physical_address", "Physical Business Address", "pass",
            "Physical address detected on page.",
            regulation="Business & Professions Code §10162 — DRE licensees must maintain a definite place of business. Displaying your office address adds trust and is recommended by DRE guidelines."))
    else:
        results.append(RuleResult("physical_address", "Physical Business Address", "warn",
            "No physical business address detected.",
            detail="While not strictly required on a website, displaying your office address builds consumer trust and aligns with DRE's requirement to maintain a definite place of business.",
            fix="Consider adding your office address to the footer. A P.O. Box alone may be insufficient for some DRE contexts.",
            regulation="Business & Professions Code §10162 — Licensees must maintain a definite place of business. Displaying the address on advertising is best practice."))

    # 10. ADA accessibility
    has_ada_text = bool(ADA_RE.search(text))
    # Check for common accessibility widget scripts (UserWay, accessiBe, AudioEye, EqualWeb, etc.)
    has_ada_widget = bool(re.search(r'userway|accessibe|audioeye|equalweb|accessibilitywidget|ada\.compliance|accesswidget', html, re.I))
    has_ada_link = bool(re.search(r'href=["\'][^"\']*(/accessibility|/ada)[^"\']*["\']', html, re.I))
    if has_ada_text or has_ada_widget or has_ada_link:
        results.append(RuleResult("ada_accessibility", "ADA Accessibility Statement", "pass",
            "Accessibility statement or compliance tool found.",
            source_url="https://www.ada.gov/resources/web-guidance/",
            regulation="Americans with Disabilities Act, Title III (42 U.S.C. §12182) — Public accommodations, which courts have interpreted to include websites, must be accessible to individuals with disabilities. DOJ guidance (March 2022) confirms websites must be accessible."))
    else:
        results.append(RuleResult("ada_accessibility", "ADA Accessibility Statement", "warn",
            "No accessibility statement or compliance tool detected on this page.",
            detail="While not a DRE requirement, the ADA requires websites of public accommodations to be accessible. NAR recommends all REALTOR websites include an accessibility statement.",
            source_url="https://www.ada.gov/resources/web-guidance/",
            regulation="ADA Title III (42 U.S.C. §12182) — Websites of public accommodations must be accessible. NAR Accessibility Best Practices (2023) recommend an accessibility statement and WCAG 2.1 AA conformance.",
            fix="Add an accessibility statement page linked from your footer. State your commitment to accessibility and provide a way for users to report issues. Consider a WCAG 2.1 AA audit of your site."))

    return results


# ---------------------------------------------------------------------------
# Lending / MLO checks
# ---------------------------------------------------------------------------
def run_lending_checks(text: str, html: str, eho_signals: list = None,
                       privacy_page_text: str = "") -> list[RuleResult]:
    results = []

    # 1. TILA / Reg Z APR proximity
    results.append(check_tila_proximity(text))

    # 2. NMLS number
    if NMLS_RE.search(text):
        results.append(RuleResult("safe_nmls", "SAFE Act — NMLS Number Display", "pass",
            "NMLS number found on page.",
            source_url="https://mortgage.nationwidelicensingsystem.org/about/policies/Pages/NMLSPolicyGuidebook.aspx",
            regulation="SAFE Act (12 U.S.C. §5102-5116, §5103(3)) — 'The term \"unique identifier\" means a number or other identifier that... is assigned by the Nationwide Mortgage Licensing System.' NMLS Policy Guidebook §5.1 — 'Each individual and company must display their NMLS Unique Identifier on all residential mortgage loan advertising.'"))
    else:
        results.append(RuleResult("safe_nmls", "SAFE Act — NMLS Number Display", "fail",
            "No NMLS identification number detected on this page.",
            detail="The SAFE Act requires every mortgage loan originator to display their NMLS Unique Identifier on all advertising materials, including websites.",
            source_url="https://mortgage.nationwidelicensingsystem.org/about/policies/Pages/NMLSPolicyGuidebook.aspx",
            regulation="SAFE Act (12 U.S.C. §5103(3)) — Definition of unique identifier. 12 U.S.C. §5105(a)(1) — 'Each mortgage originator shall... furnish his or her unique identifier.' NMLS Policy Guidebook §5.1 — 'Each individual and company must display their NMLS Unique Identifier on all residential mortgage loan advertising, including business cards, websites, and all other advertising.'",
            fix="Add your individual NMLS ID and your company's NMLS ID to your website footer so they appear on every page. Use the format: 'NMLS #123456'. Both your personal NMLS ID and your company's NMLS ID must be displayed.",
            webmaster_email=WM_NMLS))

    # 3. DRE license (MLOs in CA also need DRE)
    if DRE_LICENSE_RE.search(text):
        results.append(RuleResult("dre_license_mlo", "DRE License Number (MLO)", "pass",
            "DRE license number found — required for CA-licensed MLOs.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="California Business & Professions Code §10140.6 — MLOs licensed through DRE must display their DRE license number in addition to their NMLS ID."))
    else:
        results.append(RuleResult("dre_license_mlo", "DRE License Number (MLO)", "warn",
            "No DRE license number detected. If you are DRE-licensed (not DFPI-licensed), your DRE number is required.",
            detail="California MLOs licensed through DRE must display both their DRE license number and NMLS ID.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="Business & Professions Code §10140.6 — DRE-licensed MLOs must include their license number on all advertising.",
            fix="If you are DRE-licensed, add your DRE license number (e.g. 'DRE #01234567') to your footer alongside your NMLS ID. If you are DFPI-licensed only, this check does not apply to you."))

    # 4. Equal Housing Lender
    #    IMPORTANT: For lending, we need "Equal Housing LENDER" specifically.
    #    "Equal Housing Opportunity" alone is NOT sufficient for mortgage lenders.
    #    The EHO DOM signals and image regex match both — so we filter here.
    has_lender = bool(re.search(r'equal\s+housing\s+lender', text, re.I))
    # Check images/HTML for EHL-specific filenames (expanded to catch ehl-logo, ehl_icon, etc.)
    has_lender_img = bool(re.search(
        r'equal[_\-.\s]?housing[_\-.\s]?lender|ehl[_\-.]?(?:logo|icon|badge|seal)',
        html, re.I))
    # Check DOM signals for "lender" (includes SVG nearby-text from improved JS)
    has_lender_dom = any('lender' in s.lower() for s in (eho_signals or []))
    # Check if "Equal Housing Opportunity" (the RE variant) is present instead
    has_eho_opportunity = bool(re.search(r'equal\s+housing\s+opportunity', text, re.I)) or \
                          bool(re.search(r'equal\s+housing', text, re.I)) or \
                          bool(re.search(r'fair\s+housing', text, re.I)) or \
                          bool(re.search(r'equal[_\-.\s]?housing', html, re.I)) or \
                          any(s for s in (eho_signals or []) if 'lender' not in s.lower())
    if has_lender or has_lender_img or has_lender_dom:
        results.append(RuleResult("equal_housing_lender", "Equal Housing Lender Statement", "pass",
            "Equal Housing Lender statement or logo found.",
            source_url="https://www.consumerfinance.gov/rules-policy/regulations/1002/4/",
            regulation="Regulation B (12 CFR §1002.4(b)), implementing the Equal Credit Opportunity Act (15 U.S.C. §1691) — 'A creditor that advertises credit shall include in each advertisement a statement of the creditor's compliance with the Equal Credit Opportunity Act.' For mortgage lenders, this means displaying 'Equal Housing Lender' and the Equal Housing logo."))
    elif has_eho_opportunity:
        # Site has a fair housing statement but uses "Opportunity" instead of "Lender"
        results.append(RuleResult("equal_housing_lender", "Equal Housing Lender Statement", "warn",
            "'Equal Housing Opportunity' found, but mortgage lenders should use 'Equal Housing Lender' specifically.",
            detail="Your site displays a fair housing statement, but Regulation B requires mortgage lenders to use the specific phrase 'Equal Housing Lender' rather than 'Equal Housing Opportunity'.",
            source_url="https://www.consumerfinance.gov/rules-policy/regulations/1002/4/",
            regulation="Regulation B (12 CFR §1002.4(b)) — Mortgage lenders must use 'Equal Housing Lender' specifically. 'Equal Housing Opportunity' is for real estate agents under the Fair Housing Act.",
            fix="Change 'Equal Housing Opportunity' to 'Equal Housing Lender' in your footer. Consider also adding the Equal Housing Lender logo."))
    else:
        results.append(RuleResult("equal_housing_lender", "Equal Housing Lender Statement", "fail",
            "'Equal Housing Lender' statement or logo not found on this page.",
            detail="Mortgage lender advertising must include 'Equal Housing Lender' (not just 'Equal Housing Opportunity'). This is a separate requirement under Regulation B / ECOA.",
            source_url="https://www.consumerfinance.gov/rules-policy/regulations/1002/4/",
            regulation="Regulation B (12 CFR §1002.4(b)) — 'A creditor shall provide the appropriate notice to an applicant.' For advertising, creditors must include: 'Equal Housing Lender' or the Equal Housing Lender logo. The Federal Reserve Board's Official Staff Commentary confirms this applies to all forms of advertising, including internet advertising.",
            fix="Display the words 'Equal Housing Lender' and the Equal Housing Lender logo in your website footer. Note: 'Equal Housing Opportunity' alone is not sufficient for mortgage lenders — you must specifically use 'Equal Housing Lender.'",
            webmaster_email=WM_EHL))

    # 5. CCPA privacy policy  (search both text AND HTML links, like realestate version)
    #    Also check privacy_page_text if the subpage was followed
    all_privacy_text = text + " " + privacy_page_text
    has_privacy = CCPA_RE.search(text) or \
                  bool(re.search(r'(href|link|url).*?privacy[\-_\s]?policy|privacy[\-_\s]?policy.*?(href|link|url)', html, re.I)) or \
                  _has_own_privacy_link(html)
    has_dns     = DO_NOT_SELL_RE.search(all_privacy_text) or \
                  bool(re.search(r'do[\-_\s]*not[\-_\s]*sell', html, re.I))
    has_ccpa_on_subpage = bool(re.search(
        r'california\s+consumer\s+privacy|ccpa|cpra|right\s+to\s+(?:know|delete|opt)|california\s+privacy\s+rights',
        privacy_page_text, re.I)) if privacy_page_text else False
    if has_ccpa_on_subpage:
        has_dns = has_dns or bool(re.search(r'do\s+not\s+sell|opt[\-\s]out', privacy_page_text, re.I))
    if has_privacy:
        if has_dns:
            results.append(RuleResult("ccpa_privacy", "CCPA/CPRA Privacy Policy", "pass",
                "Privacy policy and 'Do Not Sell' language found.",
                source_url="https://oag.ca.gov/privacy/ccpa",
                regulation="California Consumer Privacy Act (Civil Code §1798.100-199), as amended by CPRA — Full compliance detected."))
        else:
            results.append(RuleResult("ccpa_privacy", "CCPA/CPRA Privacy Policy", "warn",
                "Privacy policy found, but no 'Do Not Sell or Share' link detected.",
                source_url="https://oag.ca.gov/privacy/ccpa",
                regulation="Civil Code §1798.120(a) & §1798.135 — If you sell or share personal information, a 'Do Not Sell or Share My Personal Information' link is required.",
                fix="Add a 'Do Not Sell or Share My Personal Information' link to your footer if you share any user data with third parties."))
    else:
        results.append(RuleResult("ccpa_privacy", "CCPA/CPRA Privacy Policy", "fail",
            "No privacy policy detected on this page.",
            detail="Mortgage businesses collecting personal information (loan applications, contact forms, etc.) must have a CCPA-compliant privacy policy. Penalties are $2,500 per unintentional violation, $7,500 per intentional violation.",
            source_url="https://oag.ca.gov/privacy/ccpa",
            regulation="California Consumer Privacy Act (Civil Code §1798.100) — 'A business that collects a consumer's personal information shall, at or before the point of collection, inform consumers as to the categories of personal information to be collected and the purposes for which the categories of personal information are collected or used.'",
            fix="Create a comprehensive Privacy Policy page linked from your footer. For mortgage businesses, also ensure Gramm-Leach-Bliley Act (GLBA) privacy notice requirements are met.",
            webmaster_email=WM_CCPA))

    # 6. DFPI prohibited claims
    prohibited = re.compile(
        r'\b(guaranteed\s+approv|no\s+credit\s+check|instant\s+approv|always\s+approv|100%\s+financ|no\s+money\s+down\s+mortgage|pre.?approved\s+for\s+any)\b', re.I
    )
    hit = prohibited.search(text)
    if hit:
        results.append(RuleResult("dfpi_prohibited", "DFPI — Prohibited Advertising Claims", "fail",
            "Potentially prohibited advertising claim detected.",
            detail=f"Found: '{hit.group()[:80]}'. DFPI prohibits misleading claims in mortgage advertising, including guarantees of approval, claims of no credit checks, and similar language.",
            source_url="https://dfpi.ca.gov/licensees/mortgage-lending/advertising/",
            regulation="California Financial Code §22161 — 'No licensee shall make, publish, disseminate, circulate, or place before the public an advertisement or marketing material that includes any false, misleading, or deceptive statement or representation.' DFPI Advertising Guidelines specifically prohibit: (1) Claims of guaranteed approval, (2) 'No credit check' claims, (3) Claims of instant or automatic approval, (4) Any claim that implies all applicants will be approved regardless of creditworthiness.",
            fix=f"Remove or rewrite the phrase '{hit.group()[:60]}'. Replace with compliant language. For example, instead of 'guaranteed approval,' use 'subject to credit approval and underwriting guidelines.' Instead of 'no credit check,' explain your actual underwriting process honestly."))
    else:
        results.append(RuleResult("dfpi_prohibited", "DFPI — Prohibited Advertising Claims", "pass",
            "No prohibited DFPI advertising claims detected.",
            source_url="https://dfpi.ca.gov/licensees/mortgage-lending/advertising/",
            regulation="California Financial Code §22161 — Advertising must not be false, misleading, or deceptive. No prohibited terms detected."))

    # 7. Contact information  (check HTML mailto: links and contact page links too)
    has_phone = bool(re.search(r'\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}', text)) and not PLACEHOLDER_PHONE_RE.search(text)
    has_email = _has_real_email(text, html)
    if has_phone and has_email:
        results.append(RuleResult("contact_info", "Contact Information", "pass",
            "Phone and email contact found on page.",
            regulation="NMLS Policy Guidebook and SAFE Act best practices — Consumers should be able to reach the licensed MLO directly."))
    elif has_phone or has_email:
        results.append(RuleResult("contact_info", "Contact Information", "warn",
            f"{'Phone' if has_phone else 'Email'} found, but {'email' if has_phone else 'phone'} not detected.",
            fix="Add both a phone number and email address for consumer contact.",
            regulation="Best practice — Multiple contact methods increase consumer trust and accessibility."))
    else:
        results.append(RuleResult("contact_info", "Contact Information", "fail",
            "No phone number or email address detected.",
            fix="Add your phone number and email address to the website footer.",
            regulation="SAFE Act and NMLS best practices require consumers to be able to contact the licensed MLO."))

    # 8. ADA accessibility
    has_ada_text = bool(ADA_RE.search(text))
    has_ada_widget = bool(re.search(r'userway|accessibe|audioeye|equalweb|accessibilitywidget|ada\.compliance|accesswidget', html, re.I))
    has_ada_link = bool(re.search(r'href=["\'][^"\']*(/accessibility|/ada)[^"\']*["\']', html, re.I))
    if has_ada_text or has_ada_widget or has_ada_link:
        results.append(RuleResult("ada_accessibility", "ADA Accessibility Statement", "pass",
            "Accessibility statement or compliance tool found.",
            source_url="https://www.ada.gov/resources/web-guidance/",
            regulation="ADA Title III (42 U.S.C. §12182) — Websites of public accommodations must be accessible to individuals with disabilities."))
    else:
        results.append(RuleResult("ada_accessibility", "ADA Accessibility Statement", "warn",
            "No accessibility statement or compliance tool detected.",
            detail="The ADA requires websites of public accommodations (which includes financial services) to be accessible. CFPB has also emphasized digital accessibility for mortgage servicers.",
            source_url="https://www.ada.gov/resources/web-guidance/",
            regulation="ADA Title III (42 U.S.C. §12182) — Public accommodations must be accessible. CFPB has issued guidance on digital accessibility for financial services websites.",
            fix="Add an accessibility statement page linked from your footer. Consider a WCAG 2.1 AA audit."))

    # 9. NMLS Consumer Access link
    has_consumer_access = bool(re.search(r'nmlsconsumeraccess|consumer\s*access', text, re.I))
    if has_consumer_access:
        results.append(RuleResult("nmls_consumer_access", "NMLS Consumer Access Link", "pass",
            "Link to NMLS Consumer Access found.",
            source_url="https://www.nmlsconsumeraccess.org/",
            regulation="NMLS Policy Guidebook §5.1 — Recommended best practice to link to NMLS Consumer Access (nmlsconsumeraccess.org) so consumers can verify licensee status."))
    else:
        results.append(RuleResult("nmls_consumer_access", "NMLS Consumer Access Link", "warn",
            "No link to NMLS Consumer Access detected.",
            detail="Best practice is to link to nmlsconsumeraccess.org so consumers can verify your license status. Some states require this link.",
            source_url="https://www.nmlsconsumeraccess.org/",
            regulation="NMLS Policy Guidebook §5.1 — 'Licensees are encouraged to include a link to NMLS Consumer Access on their website.' Some states (e.g., Texas, New York) explicitly require this link.",
            fix="Add a link to https://www.nmlsconsumeraccess.org in your footer, labeled 'NMLS Consumer Access' or 'Verify My License.'"))

    return results


# ---------------------------------------------------------------------------
# Shared checks (all professions)
# ---------------------------------------------------------------------------
AI_BOTS = ["GPTBot", "ClaudeBot", "Claude-Web", "Google-Extended", "CCBot", "PerplexityBot", "Bytespider", "anthropic-ai"]

async def check_ai_crawler_blocking(final_url: str) -> RuleResult:
    """Check if robots.txt or meta tags block AI crawlers."""
    from urllib.parse import urlparse
    parsed = urlparse(final_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    blocked_bots = []
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            r = await client.get(robots_url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; ComplyWithJudy/2.0)"
            })
            if r.status_code == 200:
                robots_text = r.text.lower()
                current_agent = None
                for line in robots_text.splitlines():
                    line = line.strip()
                    if line.startswith("user-agent:"):
                        current_agent = line.split(":", 1)[1].strip()
                    elif line.startswith("disallow:") and current_agent:
                        path = line.split(":", 1)[1].strip()
                        if path == "/" or path == "/*":
                            for bot in AI_BOTS:
                                if bot.lower() == current_agent or current_agent == "*":
                                    # Only flag wildcard block if it's clearly targeting AI
                                    if current_agent != "*":
                                        blocked_bots.append(bot)
    except Exception as e:
        log.debug(f"robots.txt fetch failed for {robots_url}: {e}")

    # Deduplicate
    blocked_bots = list(set(blocked_bots))

    if not blocked_bots:
        return RuleResult(
            id="ai_crawler_access",
            name="AI Crawler Access",
            status="pass",
            description="Your site allows AI-powered search tools to index your content.",
            source_url="https://developers.google.com/search/docs/crawling-indexing/overview-google-crawlers",
            regulation="While not legally required, blocking AI crawlers (GPTBot, ClaudeBot, Google-Extended) removes your site from AI-powered search results including ChatGPT, Perplexity, Google AI Overviews, and other tools that increasingly drive real estate leads.",
            fix="No action needed. Your site is discoverable by AI search tools."
        )

    bot_list = ", ".join(blocked_bots)
    return RuleResult(
        id="ai_crawler_access",
        name="AI Crawler Access",
        status="warn",
        description=f"Your robots.txt blocks AI crawlers: {bot_list}. This reduces your visibility in AI-powered search.",
        source_url="https://developers.google.com/search/docs/crawling-indexing/overview-google-crawlers",
        regulation="Blocking AI crawlers is your right, but it comes at a cost. AI-powered tools like ChatGPT, Google AI Overviews, and Perplexity are increasingly how consumers find real estate agents. Blocking these bots means your listings, reviews, and content won't appear in AI-generated answers — your competitors will show up instead.",
        fix=f"Edit your robots.txt file and remove or modify the Disallow rules for: {bot_list}. If you're using Cloudflare, go to Security > Bots and disable 'Block AI Scrapers and Crawlers.' If your hosting provider set this up, ask them to allow AI crawlers while keeping malicious bot protection.",
        webmaster_email=_webmaster_email(
            "SEO: Our Website Is Blocking AI Search Crawlers",
            f"Hi,\n\nOur robots.txt is currently blocking the following AI crawlers: {bot_list}\n\nThis means our site won't appear in AI-powered search results (ChatGPT, Google AI Overviews, Perplexity, etc.). These tools are increasingly how consumers search for real estate agents and listings.\n\nPlease update robots.txt to allow these crawlers. If we're using Cloudflare, the setting is under Security > Bots > 'Block AI Scrapers and Crawlers' — turn it off.\n\nOur competitors are showing up in these AI results and we're not. Let's fix this ASAP.\n\nPlease confirm when this has been updated."
        ),
    )


def score_results(results: list[RuleResult]) -> int:
    """Calculate 0-100 compliance score with weighted severity."""
    scorable = [r for r in results if r.status != "skip"]
    if not scorable:
        return 100
    points = sum({"pass": 10, "warn": 5, "fail": 0}.get(r.status, 5) for r in scorable)
    return round((points / (len(scorable) * 10)) * 100)


# ---------------------------------------------------------------------------
# Email notification (calls Netlify function)
# ---------------------------------------------------------------------------
EMAIL_FUNCTION_URL = os.getenv("EMAIL_FUNCTION_URL", "https://complywithjudy.com/.netlify/functions/send-email")

async def send_scan_email(email: str, scan_id: str, response: dict, is_paid: bool = False):
    """Fire-and-forget email notification after scan completes."""
    try:
        checks = response.get("checks", [])
        passed = sum(1 for c in checks if c["status"] == "pass")
        warnings = sum(1 for c in checks if c["status"] == "warn")
        failed = sum(1 for c in checks if c["status"] == "fail")
        total = sum(1 for c in checks if c["status"] != "skip")

        payload = {
            "to": email,
            "scanId": scan_id,
            "url": response.get("url", ""),
            "score": response.get("score", 0),
            "profession": response.get("profession", "realestate"),
            "passed": passed,
            "warnings": warnings,
            "failed": failed,
            "totalChecks": total,
            "isPaid": is_paid,
            "checks": [
                {
                    "name": c["name"],
                    "status": c["status"],
                    "description": c["description"],
                    "fix": c.get("fix") or "",
                }
                for c in checks if c["status"] != "skip"
            ],
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{EMAIL_FUNCTION_URL}/scan-complete",
                json=payload,
            )
            if resp.status_code == 200:
                log.info(f"Scan email sent to {email} for {scan_id}")
            else:
                log.warning(f"Scan email failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        log.warning(f"Scan email error (non-fatal): {e}")


async def send_courtesy_email(to_email: str, to_name: str, scan_id: str, response: dict):
    """Send a courtesy compliance report to a partner realtor."""
    try:
        checks = response.get("checks", [])
        passed = sum(1 for c in checks if c["status"] == "pass")
        warnings = sum(1 for c in checks if c["status"] == "warn")
        failed = sum(1 for c in checks if c["status"] == "fail")
        total = sum(1 for c in checks if c["status"] != "skip")

        payload = {
            "to": to_email,
            "toName": to_name or "",
            "scanId": scan_id,
            "url": response.get("url", ""),
            "score": response.get("score", 0),
            "profession": response.get("profession", "realestate"),
            "passed": passed,
            "warnings": warnings,
            "failed": failed,
            "totalChecks": total,
            "checks": [
                {
                    "name": c["name"],
                    "status": c["status"],
                    "description": c["description"],
                    "fix": c.get("fix") or "",
                }
                for c in checks if c["status"] != "skip"
            ],
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{EMAIL_FUNCTION_URL}/courtesy-scan",
                json=payload,
            )
            if resp.status_code == 200:
                log.info(f"Courtesy email sent to {to_email} for {scan_id}")
            else:
                log.warning(f"Courtesy email failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        log.warning(f"Courtesy email error (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Main scan endpoint
# ---------------------------------------------------------------------------
@app.post("/scan")
async def scan(req: ScanRequest, request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    url = req.url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"

    # --- Abuse check ---
    allowed, reason = await check_scan_allowed(ip, req.email, req.user_id)
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)

    scan_id = req.scan_id or str(uuid.uuid4())

    # Create the scan record in Supabase so the results page can find it
    is_free = (reason == "free")
    await create_scan_record(scan_id, url, req.profession, email=req.email, user_id=req.user_id, is_free=is_free)
    await update_scan_status(scan_id, "running")

    t0 = time.time()
    try:
        scraped = await scrape_website(url)
        text = scraped["text"]
        html = scraped["raw_html"]
        eho_signals = scraped.get("eho_signals", [])
        final_url = scraped.get("url_final", url)

        # --- Parked / for-sale domain detection ---
        # If the site redirected to a known domain parking service, short-circuit
        # with a special status instead of scoring a non-existent business.
        _PARKED_DOMAINS = [
            'forsale.godaddy.com', 'godaddy.com/forsale', 'afternic.com',
            'sedo.com', 'dan.com', 'hugedomains.com', 'bodis.com',
            'above.com', 'parkingcrew.net', 'sedoparking.com',
        ]
        _PARKED_SIGNALS = [
            'domain is for sale', 'this domain is for sale', 'buy this domain',
            'make an offer', 'domain may be for sale', 'parked free',
            'this webpage was generated by the domain owner',
        ]
        is_parked = any(d in final_url.lower() for d in _PARKED_DOMAINS) or \
                    any(s in text[:2000] for s in _PARKED_SIGNALS)
        if is_parked:
            elapsed = round(time.time() - t0, 1)
            response = {
                "scan_id": scan_id,
                "score": None,
                "url": final_url,
                "profession": req.profession,
                "elapsed_seconds": elapsed,
                "status": "parked_domain",
                "message": "This domain appears to be parked or for sale. No active business website was found to scan.",
                "checks": [],
            }
            await update_scan_status(scan_id, "completed", result=response,
                                     error_type="parked_domain",
                                     error_message="Domain is parked or for sale — no business content to scan.")
            return response

        # --- DRE lookup for realestate scans (broker identification) ---
        dre_number = None
        dre_info = None
        if req.profession != "lending":
            # Try labeled DRE number first (DRE #, CalBRE #, etc.)
            dre_match = DRE_LICENSE_RE.search(text)
            if dre_match:
                num_match = re.search(r'\d{7,9}', dre_match.group())
                if num_match:
                    dre_number = num_match.group()
                    log.info(f"DRE number found via labeled regex: #{dre_number}")
            # Fallback: find bare 8-digit numbers, skip NMLS-labeled ones
            if not dre_number:
                for m in re.finditer(r'\b(\d{8})\b', text):
                    pos = m.start()
                    look_back = text[max(0, pos-80):pos].upper()
                    if re.search(r'NMLS', look_back):
                        continue
                    if re.search(r'[\-\(\)]\s*$', look_back):
                        continue
                    # Check if this looks like a license number context
                    if re.search(r'(LIC|DRE|BRE|CALBRE|LICENSE|BROKER|#)', look_back):
                        dre_number = m.group(1)
                        log.info(f"DRE number found via bare 8-digit with context: #{dre_number}")
                        break
            if dre_number:
                dre_info = await lookup_dre_info(dre_number)
                log.info(f"DRE lookup result for #{dre_number}: type={dre_info.get('license_type')}, name={dre_info.get('name')}, officer={dre_info.get('designated_officer')}")
            else:
                log.info(f"No DRE number found in page text for {url}")

        # --- Entity classification ---
        entity_type = classify_entity(text)
        if entity_type != 'standard':
            log.info(f"Entity classified as '{entity_type}' for {url}")

        privacy_page_text = scraped.get("privacy_page_text", "")
        if req.profession == "lending":
            rule_results = run_lending_checks(text, html, eho_signals=eho_signals,
                                              privacy_page_text=privacy_page_text)
        else:
            rule_results = run_realestate_checks(text, html, eho_signals=eho_signals,
                                                  dre_number=dre_number, dre_info=dre_info,
                                                  privacy_page_text=privacy_page_text)

        # --- Skip inapplicable checks for non-standard entities ---
        if entity_type == 'nonprofit':
            for r in rule_results:
                if r.id in ('dre_license', 'responsible_broker', 'team_advertising', 'ab723_images'):
                    r.status = 'skip'
                    r.description = f"Check not applicable — site classified as nonprofit organization."
                    r.fix = ""
        elif entity_type == 'commercial_developer':
            for r in rule_results:
                if r.id in ('dre_license', 'responsible_broker', 'team_advertising', 'ab723_images'):
                    r.status = 'skip'
                    r.description = f"Check not applicable — site classified as commercial real estate developer/investor."
                    r.fix = ""
        elif entity_type == 'property_manager':
            for r in rule_results:
                if r.id in ('dre_license', 'responsible_broker', 'team_advertising'):
                    r.status = 'skip'
                    r.description = f"Check not applicable — site classified as property management company."
                    r.fix = ""
        elif entity_type == 'commercial_lender':
            for r in rule_results:
                if r.id in ('tila_apr', 'safe_nmls', 'equal_housing_lender', 'nmls_consumer_access'):
                    r.status = 'skip'
                    r.description = f"Check not applicable — site classified as commercial/SBA lender (not residential mortgage)."
                    r.fix = ""

        # --- Shared checks (both professions) ---
        ai_check = await check_ai_crawler_blocking(scraped["url_final"])
        rule_results.append(ai_check)

        # --- Subpage diagnostic: if contact_info failed and we scanned a subpage, add context ---
        from urllib.parse import urlparse
        parsed_url = urlparse(scraped["url_final"])
        is_subpage = parsed_url.path not in ("", "/", "/index.html", "/index.php")
        if is_subpage:
            for r in rule_results:
                if r.id == "contact_info" and r.status == "fail":
                    r.detail = (r.detail or "") + f" Note: this scan checked {parsed_url.path} — the homepage may have contact info that was not analyzed."

        score = score_results(rule_results)
        elapsed = round(time.time() - t0, 1)

        response = {
            "scan_id": scan_id,
            "score": score,
            "url": scraped["url_final"],
            "profession": req.profession,
            "entity_type": entity_type,
            "platform": scraped.get("platform", "unknown"),
            "status": "completed",
            "is_free_scan": (reason == "free"),
            "elapsed_seconds": elapsed,
            "screenshot_available": True,
            "checks": [
                {
                    "id": r.id,
                    "name": r.name,
                    "status": r.status,
                    "description": r.description,
                    "detail": r.detail,
                    "source_url": r.source_url,
                    "fix": r.fix,
                    "regulation": r.regulation,
                    "webmaster_email": r.webmaster_email,
                }
                for r in rule_results
            ]
        }

        await update_scan_status(scan_id, "completed", result=response)

        # Populate review queue for ambiguous results (fire-and-forget)
        try:
            await _populate_review_queue(
                scan_id=scan_id,
                site_url=url,
                page_url=scraped.get("url_final", url),
                profession=req.profession,
                entity_type=entity_type,
                score=score,
                rule_results=rule_results,
                screenshot_hex=scraped.get("screenshot_hex", ""),
                dre_info=dre_info,
            )
        except Exception as e:
            log.warning(f"Review queue population failed (non-blocking): {e}")

        # Record fingerprint for free scans
        if is_free and scan_id:
            await record_fingerprint(ip, req.email, scan_id)

        # Send email notification (fire-and-forget)
        await send_scan_email(req.email, response["scan_id"], response, is_paid=not is_free)

        # Courtesy scan — send full report to partner (admin only)
        if req.courtesy_to and req.email.lower().strip() in ADMIN_EMAILS:
            await send_courtesy_email(req.courtesy_to, req.courtesy_name or "", response["scan_id"], response)

        return response

    except Exception as exc:
        error_type = classify_error(exc)
        error_msg  = ERROR_MESSAGES[error_type]
        log.error(f"Scan failed [{error_type}] {url}: {exc}")
        await update_scan_status(scan_id, "failed", error_type=error_type.value, error_message=error_msg)
        raise HTTPException(status_code=422, detail={"error_type": error_type.value, "message": error_msg})


# --- Get scan result by ID (for email "View Report" links) ---
@app.get("/scan/{scan_id}")
async def get_scan(scan_id: str):
    """Return scan result by ID — used by the Results page when opened from an email link."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/scans",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            },
            params={"id": f"eq.{scan_id}", "select": "*"}
        )
        rows = r.json()

    if not rows:
        raise HTTPException(status_code=404, detail="Result not found")

    row = rows[0]

    # If scan is still running or pending, return status so frontend can poll
    if row["status"] in ("pending", "running"):
        return {"scan_id": scan_id, "status": row["status"]}

    # If scan failed, return error info
    if row["status"] == "failed":
        return {
            "scan_id": scan_id,
            "status": "failed",
            "error_type": row.get("error_type"),
            "error_message": row.get("error_message"),
            "url": row.get("url"),
        }

    # Completed — return the full result
    result = row.get("result")
    if isinstance(result, str):
        result = json.loads(result)

    if result:
        # Ensure scan_id is in the result
        result["scan_id"] = scan_id
        return result

    # Fallback: return basic info from the row
    return {
        "scan_id": scan_id,
        "score": row.get("score", 0),
        "url": row.get("url"),
        "profession": row.get("profession"),
        "status": "completed",
        "is_free_scan": row.get("is_free_scan", True),
        "checks": [],
    }


# ---------------------------------------------------------------------------
# Admin Review Queue Endpoints
# ---------------------------------------------------------------------------

@app.get("/admin/queue")
async def admin_list_queue(request: Request):
    """List review queue items with filters and pagination."""
    admin_id = await verify_admin(request)

    # Parse query params
    params = dict(request.query_params)
    review_status = params.get("review_status", "")
    rule_id = params.get("rule_id")
    profession = params.get("profession")
    bug_tag = params.get("bug_tag")
    claimed_by = params.get("claimed_by")
    page = int(params.get("page", "0"))
    per_page = min(int(params.get("per_page", "50")), 100)

    # Build Supabase query
    query_params = {
        "select": "*",
        "order": "created_at.desc",
        "offset": str(page * per_page),
        "limit": str(per_page),
    }
    if review_status:
        query_params["review_status"] = f"eq.{review_status}"
    if rule_id:
        query_params["rule_id"] = f"eq.{rule_id}"
    if profession:
        query_params["profession"] = f"eq.{profession}"
    if bug_tag:
        query_params["bug_tag"] = f"eq.{bug_tag}"
    if claimed_by:
        query_params["claimed_by"] = f"eq.{claimed_by}"

    supabase_headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }

    # Get items
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/review_queue",
            headers=supabase_headers,
            params=query_params,
        )
    items = r.json() if r.status_code < 300 else []
    if not isinstance(items, list):
        items = []

    # Get total count separately
    count_params = {k: v for k, v in query_params.items() if k not in ("offset", "limit", "order", "select")}
    count_params["select"] = "id"
    async with httpx.AsyncClient(timeout=10) as client:
        r2 = await client.get(
            f"{SUPABASE_URL}/rest/v1/review_queue",
            headers={**supabase_headers, "Prefer": "count=exact", "Range": "0-0"},
            params=count_params,
        )
    content_range = r2.headers.get("content-range", "")
    total = int(content_range.split("/")[-1]) if "/" in content_range else len(items)

    return {"items": items, "total": total, "page": page, "per_page": per_page}


@app.get("/admin/queue/stats")
async def admin_queue_stats(request: Request):
    """Get aggregate queue statistics."""
    await verify_admin(request)
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/review_queue_stats",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
        )
    return r.json() if r.status_code == 200 else []


@app.get("/admin/queue/{item_id}")
async def admin_get_queue_item(item_id: str, request: Request):
    """Get review queue item detail with assets and full scan context."""
    await verify_admin(request)

    # Get the queue item
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/review_queue",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            params={"id": f"eq.{item_id}", "select": "*"},
        )
    items = r.json()
    if not items:
        raise HTTPException(404, "Review item not found")
    item = items[0]

    # Get assets
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/review_assets",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            params={"review_item_id": f"eq.{item_id}", "select": "*"},
        )
    assets = r.json() if r.status_code == 200 else []

    # Get full scan result for context (all rules from the same scan)
    scan_context = None
    if item.get("scan_id"):
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/scans",
                headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                params={"id": f"eq.{item['scan_id']}", "select": "result"},
            )
            scans = r.json()
            if scans and scans[0].get("result"):
                result = scans[0]["result"]
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except:
                        pass
                scan_context = result

    # Fallback: if no scan_context (scan_id missing), build context from all queue items for this site
    if not scan_context and item.get("site_url"):
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/review_queue",
                headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                params={"site_url": f"eq.{item['site_url']}", "select": "*", "order": "rule_id"},
            )
            if r.status_code == 200:
                siblings = r.json()
                # Build a synthetic scan_context that the frontend can use
                scan_context = {
                    "score": item.get("score"),
                    "url": item.get("site_url"),
                    "url_final": item.get("page_url"),
                    "profession": item.get("profession"),
                    "entity_type": item.get("entity_type"),
                    "checks": [
                        {
                            "id": s["rule_id"],
                            "name": s["rule_name"],
                            "status": s["scanner_status"],
                            "detail": s.get("scanner_detail", ""),
                            "description": s.get("scanner_detail", ""),
                        }
                        for s in siblings
                    ],
                }

    return {"item": item, "assets": assets, "scan_context": scan_context}


class ReviewDecision(BaseModel):
    decision: Optional[str] = None
    reviewer_note: Optional[str] = None
    bug_tag: Optional[str] = None
    review_status: Optional[str] = None
    broker_info: Optional[dict] = None


@app.patch("/admin/queue/{item_id}")
async def admin_decide_queue_item(item_id: str, body: ReviewDecision, request: Request):
    """Submit a review decision for a queue item."""
    admin_id = await verify_admin(request)

    payload = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if body.decision:
        payload["decision"] = body.decision
        payload["reviewer_id"] = admin_id if admin_id != "api_key_admin" else None
        payload["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        payload["review_status"] = "completed"
    if body.reviewer_note is not None:
        payload["reviewer_note"] = body.reviewer_note
    if body.bug_tag is not None:
        payload["bug_tag"] = body.bug_tag
    if body.review_status:
        payload["review_status"] = body.review_status
    if body.broker_info:
        payload["broker_info"] = body.broker_info

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/review_queue",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            params={"id": f"eq.{item_id}"},
            json=payload,
        )
    if r.status_code >= 300:
        raise HTTPException(r.status_code, f"Failed to update: {r.text[:200]}")

    # Auto-close sibling items when marking a site as not_applicable + entity_misclass
    # or any site-wide decision (not_applicable, needs_rescan with entity tag)
    if body.decision == "not_applicable" and body.bug_tag in ("entity_misclass", "parked_domain"):
        # Get the site_url for this item
        async with httpx.AsyncClient(timeout=10) as client:
            r2 = await client.get(
                f"{SUPABASE_URL}/rest/v1/review_queue",
                headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                params={"id": f"eq.{item_id}", "select": "site_url"},
            )
        items = r2.json() if r2.status_code < 300 else []
        if items:
            site_url = items[0]["site_url"]
            # Close all other pending/claimed items for the same site
            sibling_payload = {
                "decision": body.decision,
                "reviewer_id": admin_id if admin_id != "api_key_admin" else None,
                "reviewer_note": f"Auto-closed: site marked as {body.bug_tag}",
                "bug_tag": body.bug_tag,
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "review_status": "completed",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            async with httpx.AsyncClient(timeout=10) as client:
                r3 = await client.patch(
                    f"{SUPABASE_URL}/rest/v1/review_queue",
                    headers={
                        "apikey": SUPABASE_SERVICE_KEY,
                        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                        "Content-Type": "application/json",
                        "Prefer": "return=minimal",
                    },
                    params={
                        "site_url": f"eq.{site_url}",
                        "id": f"neq.{item_id}",
                        "review_status": "in.(pending,claimed)",
                    },
                    json=sibling_payload,
                )
            log.info(f"Auto-closed sibling items for {site_url} ({body.bug_tag})")

    return {"ok": True}


@app.patch("/admin/queue/{item_id}/claim")
async def admin_claim_queue_item(item_id: str, request: Request):
    """Claim a review item (sets claimed_by and review_status='claimed')."""
    admin_id = await verify_admin(request)
    payload = {
        "review_status": "claimed",
        "claimed_by": admin_id if admin_id != "api_key_admin" else None,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/review_queue",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            params={"id": f"eq.{item_id}"},
            json=payload,
        )
    return {"ok": True}


@app.patch("/admin/queue/{item_id}/release")
async def admin_release_queue_item(item_id: str, request: Request):
    """Release a claimed review item back to pending."""
    await verify_admin(request)
    payload = {
        "review_status": "pending",
        "claimed_by": None,
        "claimed_at": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/review_queue",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            params={"id": f"eq.{item_id}"},
            json=payload,
        )
    return {"ok": True}


class BulkDecision(BaseModel):
    item_ids: list[str]
    decision: str
    reviewer_note: Optional[str] = None
    bug_tag: Optional[str] = None


@app.post("/admin/queue/bulk")
async def admin_bulk_decide(body: BulkDecision, request: Request):
    """Bulk decide multiple review items at once."""
    admin_id = await verify_admin(request)
    payload = {
        "decision": body.decision,
        "reviewer_id": admin_id if admin_id != "api_key_admin" else None,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "review_status": "completed",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if body.reviewer_note:
        payload["reviewer_note"] = body.reviewer_note
    if body.bug_tag:
        payload["bug_tag"] = body.bug_tag

    updated = 0
    for item_id in body.item_ids:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/review_queue",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                params={"id": f"eq.{item_id}"},
                json=payload,
            )
            if r.status_code < 300:
                updated += 1
    return {"ok": True, "updated": updated}


@app.post("/admin/queue/{item_id}/assets")
async def admin_upload_asset(item_id: str, request: Request):
    """Upload a screenshot or attachment for a review item."""
    admin_id = await verify_admin(request)

    # Try multipart form first, fall back to raw body
    content = None
    filename = "upload.jpg"
    mime_type = "image/jpeg"
    try:
        form = await request.form()
        file = form.get("file")
        if file:
            content = await file.read()
            filename = getattr(file, "filename", "upload.jpg") or "upload.jpg"
            mime_type = getattr(file, "content_type", "image/jpeg") or "image/jpeg"
    except Exception:
        pass

    if not content:
        # Fallback: read raw body
        content = await request.body()
        ct = request.headers.get("content-type", "image/jpeg")
        if "image/png" in ct:
            mime_type = "image/png"
            filename = "upload.png"

    if not content or len(content) < 100:
        raise HTTPException(400, "No file provided or file too small")

    storage_filename = f"{item_id}_{filename}".replace(" ", "_")
    storage_path = f"review-assets/{storage_filename}"

    # Upload to Supabase Storage (use upsert to allow overwrite)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{SUPABASE_URL}/storage/v1/object/{storage_path}",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": mime_type,
                "x-upsert": "true",
            },
            content=content,
        )
    if r.status_code >= 300:
        log.warning(f"Storage upload failed: {r.status_code} {r.text[:300]}")
        raise HTTPException(500, f"Storage upload failed: {r.text[:200]}")

    # Create asset record
    asset = {
        "review_item_id": item_id,
        "asset_type": "screenshot",
        "storage_path": storage_path,
        "filename": filename,
        "mime_type": mime_type,
        "uploaded_by": admin_id if admin_id != "api_key_admin" else None,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/review_assets",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json=[asset],
        )
    created = r.json() if r.status_code < 300 else []
    return {"ok": True, "asset": created[0] if created else asset}


@app.post("/admin/queue/populate")
async def admin_populate_queue(request: Request):
    """Manually populate review queue from a specific scan."""
    admin_id = await verify_admin(request)
    body = await request.json()
    scan_id = body.get("scan_id")
    rule_ids = body.get("rule_ids")  # optional filter

    if not scan_id:
        raise HTTPException(400, "scan_id required")

    # Get the scan result
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/scans",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            params={"id": f"eq.{scan_id}", "select": "url,result,status"},
        )
    scans = r.json()
    if not scans:
        raise HTTPException(404, "Scan not found")

    scan = scans[0]
    result = scan.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except:
            raise HTTPException(400, "Scan result is not valid JSON")

    if not result or not result.get("checks"):
        raise HTTPException(400, "Scan has no check results")

    # Build RuleResult-like objects from the stored JSON
    checks = result.get("checks", [])
    items_to_insert = []
    for c in checks:
        if rule_ids and c["id"] not in rule_ids:
            continue
        if c["status"] not in ("fail", "warn"):
            continue
        items_to_insert.append({
            "scan_id": scan_id,
            "site_url": scan.get("url", result.get("url", "")),
            "page_url": result.get("url", ""),
            "profession": result.get("profession", ""),
            "entity_type": result.get("entity_type", "standard"),
            "score": result.get("score"),
            "rule_id": c["id"],
            "rule_name": c["name"],
            "scanner_status": c["status"],
            "scanner_detail": f"{c.get('description', '')} {c.get('detail', '')}".strip(),
            "scanner_evidence": c.get("detail", "")[:500],
            "scanner_version": SCANNER_VERSION,
            "rule_version": SCANNER_VERSION,
            "review_status": "pending",
            "source": "manual",
        })

    if not items_to_insert:
        return {"ok": True, "inserted": 0, "message": "No fail/warn checks to queue"}

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/review_queue",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=ignore-duplicates",
            },
            json=items_to_insert,
        )
    return {"ok": True, "inserted": len(items_to_insert)}


# --- Health check ---
@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


# --- Retry endpoint (fixes the dashboard retry button) ---
@app.post("/scan/retry/{scan_id}")
async def retry_scan(scan_id: str, request: Request):
    """Look up an existing scan and re-run it."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/scans",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            params={"id": f"eq.{scan_id}", "select": "url,profession,user_id,email"}
        )
        rows = r.json()
    if not rows:
        raise HTTPException(status_code=404, detail="Scan not found")
    row = rows[0]
    return await scan(
        ScanRequest(url=row["url"], profession=row["profession"], email=row.get("email",""), scan_id=scan_id, user_id=row.get("user_id")),
        request
    )


# ---------------------------------------------------------------------------
# Public API  (API key auth — for Judy/OpenClaw, external integrations)
# ---------------------------------------------------------------------------
class ApiScanRequest(BaseModel):
    url: str
    profession: str              # "realestate" | "lending" — required, no default
    email: Optional[str] = None  # optional — for sending results email

def verify_api_key(request: Request):
    """Check X-API-Key header against allowed keys."""
    key = request.headers.get("X-API-Key", "")
    if not API_KEYS:
        raise HTTPException(status_code=503, detail="API keys not configured. Set API_KEYS env var on the scanner.")
    if key not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid or missing API key. Pass X-API-Key header.")

@app.post("/api/scan")
async def api_scan(req: ApiScanRequest, request: Request):
    """
    Public API scan endpoint — requires X-API-Key header.
    Bypasses free scan limits. Returns full results with fix instructions.
    """
    verify_api_key(request)

    url = req.url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"

    scan_id = str(uuid.uuid4())

    # Create scan record
    await create_scan_record(scan_id, url, req.profession, email=req.email, is_free=False)
    await update_scan_status(scan_id, "running")

    t0 = time.time()
    try:
        scraped = await scrape_website(url)
        text = scraped["text"]
        html = scraped["raw_html"]
        eho_signals = scraped.get("eho_signals", [])
        final_url = scraped.get("url_final", url)

        # --- DRE lookup for realestate scans (broker identification) ---
        dre_number = None
        dre_info = None
        if req.profession != "lending":
            dre_match = DRE_LICENSE_RE.search(text)
            if dre_match:
                num_match = re.search(r'\d{7,9}', dre_match.group())
                if num_match:
                    dre_number = num_match.group()
                    log.info(f"[api_scan] DRE number found via labeled regex: #{dre_number}")
            if not dre_number:
                for m in re.finditer(r'\b(\d{8})\b', text):
                    pos = m.start()
                    look_back = text[max(0, pos-80):pos].upper()
                    if re.search(r'NMLS', look_back):
                        continue
                    if re.search(r'[\-\(\)]\s*$', look_back):
                        continue
                    if re.search(r'(LIC|DRE|BRE|CALBRE|LICENSE|BROKER|#)', look_back):
                        dre_number = m.group(1)
                        log.info(f"[api_scan] DRE number found via bare 8-digit: #{dre_number}")
                        break
            if dre_number:
                dre_info = await lookup_dre_info(dre_number)
                log.info(f"[api_scan] DRE lookup #{dre_number}: type={dre_info.get('license_type')}, name={dre_info.get('name')}")
            else:
                log.info(f"[api_scan] No DRE number found for {url}")

        # --- Entity classification ---
        entity_type = classify_entity(text)
        if entity_type != 'standard':
            log.info(f"[api_scan] Entity classified as '{entity_type}' for {url}")

        privacy_page_text = scraped.get("privacy_page_text", "")
        if req.profession == "lending":
            rule_results = run_lending_checks(text, html, eho_signals=eho_signals,
                                              privacy_page_text=privacy_page_text)
        else:
            rule_results = run_realestate_checks(text, html, eho_signals=eho_signals,
                                                  dre_number=dre_number, dre_info=dre_info,
                                                  privacy_page_text=privacy_page_text)

        # --- Skip inapplicable checks for non-standard entities ---
        if entity_type == 'nonprofit':
            for r in rule_results:
                if r.id in ('dre_license', 'responsible_broker', 'team_advertising', 'ab723_images'):
                    r.status = 'skip'
                    r.description = f"Check not applicable — site classified as nonprofit organization."
                    r.fix = ""
        elif entity_type == 'commercial_developer':
            for r in rule_results:
                if r.id in ('dre_license', 'responsible_broker', 'team_advertising', 'ab723_images'):
                    r.status = 'skip'
                    r.description = f"Check not applicable — site classified as commercial real estate developer/investor."
                    r.fix = ""
        elif entity_type == 'property_manager':
            for r in rule_results:
                if r.id in ('dre_license', 'responsible_broker', 'team_advertising'):
                    r.status = 'skip'
                    r.description = f"Check not applicable — site classified as property management company."
                    r.fix = ""
        elif entity_type == 'commercial_lender':
            for r in rule_results:
                if r.id in ('tila_apr', 'safe_nmls', 'equal_housing_lender', 'nmls_consumer_access'):
                    r.status = 'skip'
                    r.description = f"Check not applicable — site classified as commercial/SBA lender (not residential mortgage)."
                    r.fix = ""

        ai_check = await check_ai_crawler_blocking(final_url)
        rule_results.append(ai_check)

        # --- Subpage diagnostic ---
        from urllib.parse import urlparse
        parsed_url = urlparse(final_url)
        is_subpage = parsed_url.path not in ("", "/", "/index.html", "/index.php")
        if is_subpage:
            for r in rule_results:
                if r.id == "contact_info" and r.status == "fail":
                    r.detail = (r.detail or "") + f" Note: this scan checked {parsed_url.path} — the homepage may have contact info."

        score = score_results(rule_results)
        elapsed = round(time.time() - t0, 1)

        response = {
            "scan_id": scan_id,
            "score": score,
            "url": final_url,
            "profession": req.profession,
            "entity_type": entity_type,
            "platform": scraped.get("platform", "unknown"),
            "status": "completed",
            "is_free_scan": False,
            "elapsed_seconds": elapsed,
            "checks": [
                {
                    "id": r.id,
                    "name": r.name,
                    "status": r.status,
                    "description": r.description,
                    "detail": r.detail,
                    "source_url": r.source_url,
                    "fix": r.fix,
                    "regulation": r.regulation,
                    "webmaster_email": r.webmaster_email,
                }
                for r in rule_results
            ]
        }

        await update_scan_status(scan_id, "completed", result=response)

        # Populate review queue for ambiguous results (fire-and-forget)
        try:
            await _populate_review_queue(
                scan_id=scan_id,
                site_url=req.url.strip(),
                page_url=final_url,
                profession=req.profession,
                entity_type=entity_type,
                score=score,
                rule_results=rule_results,
                screenshot_hex=scraped.get("screenshot_hex", ""),
                dre_info=dre_info,
            )
        except Exception as e:
            log.warning(f"[api_scan] Review queue population failed (non-blocking): {e}")

        # Send email if provided
        if req.email:
            await send_scan_email(req.email, scan_id, response, is_paid=True)

        return response

    except Exception as exc:
        error_type = classify_error(exc)
        error_msg = ERROR_MESSAGES[error_type]
        log.error(f"API scan failed [{error_type}] {url}: {exc}")
        await update_scan_status(scan_id, "failed", error_type=error_type.value, error_message=error_msg)
        raise HTTPException(status_code=422, detail={"error_type": error_type.value, "message": error_msg})
