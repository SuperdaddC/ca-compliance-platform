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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
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
    ScanErrorType.BLOCKED:      "The website blocked our scanner (firewall or CAPTCHA). This is common on large brokerages. Contact support for a manual review option.",
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
    if "403" in msg or "captcha" in msg or "forbidden" in msg:
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

ADMIN_EMAILS = {"mike@thecolyerteam.com"}

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
            user_agent="Mozilla/5.0 (compatible; ComplyWithJudy/2.0; +https://complywithjudy.com/bot)"
        )
        try:
            page = await context.new_page()

            # Navigate with a generous timeout; catch specific errors
            try:
                await page.goto(url, wait_until="networkidle", timeout=45000)
            except PlaywrightTimeout:
                raise TimeoutError(f"timeout loading {url}")

            # Scroll to trigger lazy-loaded footer content (where disclosures often live)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

            inner_text = await page.evaluate("() => document.body.innerText") or ""
            raw_html   = await page.evaluate("() => document.body.innerHTML") or ""
            head_html  = await page.evaluate("() => document.head ? document.head.innerHTML : ''") or ""

            # Strip tags, collapse whitespace
            def strip(h): return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', h)).strip()

            stripped_html = strip(raw_html)
            head_text     = strip(head_html)
            combined      = f"{inner_text}\n{stripped_html}\n{head_text}".lower()

            if len(combined.strip()) < 100:
                raise ValueError("empty_page: page rendered with no content")

            # Screenshot for verification feature
            screenshot_bytes = await page.screenshot(type="jpeg", quality=70, full_page=False)
            screenshot_b64 = screenshot_bytes.hex()

            # Count pages linked (basic multi-page signal)
            links = await page.evaluate(
                "() => [...new Set([...document.querySelectorAll('a[href]')].map(a=>a.href).filter(h=>h.startsWith(window.location.origin)))].length"
            )

            return {
                "text": combined,
                "raw_html": raw_html[:200000],  # cap at 200k chars
                "screenshot_hex": screenshot_b64,
                "internal_link_count": links,
                "url_final": page.url,
            }

        finally:
            await browser.close()

# ---------------------------------------------------------------------------
# Compliance rule engine  (deterministic — no LLM needed for these)
# ---------------------------------------------------------------------------

# --- Shared patterns ---
DRE_LICENSE_RE = re.compile(r'\bdre\s*#?\s*\d{7,9}\b|\bcalifornia\s+real\s+estate\s+broker\s+#?\s*\d{7,9}\b', re.I)
NMLS_RE        = re.compile(r'\bnmls\s*#?\s*\d{4,10}\b', re.I)
EQUAL_HOUSING_RE = re.compile(r'equal\s+housing\s+(opportunity|lender|logo)', re.I)
EHO_IMG_RE     = re.compile(r'(equal.housing|eho[\-_]?logo|fair.housing)', re.I)
CCPA_RE        = re.compile(r'privacy\s+policy|ccpa|do\s+not\s+sell', re.I)
ADA_RE         = re.compile(r'accessibility|ada\s+complian|wcag', re.I)

# TILA
TILA_TRIGGER_RE = re.compile(
    r'(\d+\.?\d*\s*%\s*(interest|rate|fixed|variable|arm)'
    r'|\$[\d,]+\.?\d*\s*(per\s+month|\/mo|monthly\s+payment)'
    r'|\d+\s*-?\s*year\s+(fixed|arm|loan|mortgage)'
    r'|(fixed|variable)\s+rate)',
    re.I
)
TILA_APR_RE = re.compile(r'\bapr\b', re.I)
TILA_WINDOW = 400

# DRE-specific
RESPONSIBLE_BROKER_RE = re.compile(
    r'(responsible\s+broker|supervising\s+broker|broker\s+of\s+record|dba\s+.{0,60}broker)', re.I
)
TEAM_NAME_RE = re.compile(
    r'\b(team|group|associates|partners|realty|properties)\b', re.I
)
AB723_RE = re.compile(
    r'(virtually\s+staged|digitally\s+enhanced|photo.*altered|ai.generated|image.*modified)', re.I
)
VIRTUAL_OFFICE_RE = re.compile(r'virtual\s+office|voa\b', re.I)


@dataclass
class RuleResult:
    id: str
    name: str
    status: str   # pass | fail | warn | skip
    description: str
    detail: str = ""
    source_url: str = ""
    fix: str = ""


def check_tila_proximity(text: str) -> RuleResult:
    """APR must appear within 400 chars of any TILA triggering term."""
    triggers = list(TILA_TRIGGER_RE.finditer(text))
    if not triggers:
        return RuleResult(
            id="tila_apr", name="TILA/Reg Z – APR Proximity",
            status="skip",
            description="No TILA triggering terms (rates/payments) found on page.",
            source_url="https://www.consumerfinance.gov/rules-policy/regulations/1026/"
        )
    for m in triggers:
        start = max(0, m.start() - TILA_WINDOW)
        end   = min(len(text), m.end() + TILA_WINDOW)
        window = text[start:end]
        if TILA_APR_RE.search(window):
            return RuleResult(
                id="tila_apr", name="TILA/Reg Z – APR Proximity",
                status="pass",
                description="APR appears near triggering term.",
                source_url="https://www.consumerfinance.gov/rules-policy/regulations/1026/"
            )
    return RuleResult(
        id="tila_apr", name="TILA/Reg Z – APR Proximity",
        status="fail",
        description="Triggering rate/payment terms found but APR not disclosed nearby.",
        detail=f"Found triggering term: '{triggers[0].group()[:60]}' — no APR within {TILA_WINDOW} chars.",
        source_url="https://www.consumerfinance.gov/rules-policy/regulations/1026/",
        fix="Add 'APR' disclosure immediately adjacent to any specific rate or payment amount mentioned on the page."
    )


def run_realestate_checks(text: str, html: str) -> list[RuleResult]:
    results = []

    # 1. DRE license number
    if DRE_LICENSE_RE.search(text):
        results.append(RuleResult("dre_license", "DRE License Number", "pass",
            "DRE license number found on page.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html"))
    else:
        results.append(RuleResult("dre_license", "DRE License Number", "fail",
            "No DRE license number detected.",
            detail="Your DRE license number must appear on every page of advertising.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            fix="Add your DRE license number (e.g. 'DRE #01234567') to your site footer so it appears on every page."))

    # 2. Responsible broker
    if RESPONSIBLE_BROKER_RE.search(text):
        results.append(RuleResult("responsible_broker", "Responsible Broker Name", "pass",
            "Responsible broker disclosure found.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html"))
    else:
        results.append(RuleResult("responsible_broker", "Responsible Broker Name", "fail",
            "No responsible/supervising broker identified.",
            fix="Add 'Responsible Broker: [Full Name], DRE #XXXXXXX' to your footer.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html"))

    # 3. Equal Housing Opportunity
    has_text = bool(EQUAL_HOUSING_RE.search(text))
    has_img  = bool(EHO_IMG_RE.search(html))
    if has_text or has_img:
        results.append(RuleResult("equal_housing", "Equal Housing Opportunity", "pass",
            "Equal Housing Opportunity logo or statement found.",
            source_url="https://www.hud.gov/program_offices/fair_housing_equal_opp"))
    else:
        results.append(RuleResult("equal_housing", "Equal Housing Opportunity", "fail",
            "Equal Housing Opportunity logo or statement not found.",
            fix="Display the Equal Housing Opportunity logo and/or statement on your site.",
            source_url="https://www.hud.gov/program_offices/fair_housing_equal_opp"))

    # 4. Team name requires broker affiliation
    if TEAM_NAME_RE.search(text):
        has_affiliation = bool(re.search(r'(keller williams|compass|coldwell|century 21|sotheby|exp realty|berkshire|remax|better homes|@properties)', text, re.I))
        if not has_affiliation and not RESPONSIBLE_BROKER_RE.search(text):
            results.append(RuleResult("team_advertising", "Team Advertising Compliance", "warn",
                "Team name detected but broker affiliation may not be clear.",
                detail="DRE requires team advertising to prominently display the responsible broker's name.",
                fix="Ensure your broker's name and DRE# are displayed at least as prominently as your team name.",
                source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html"))
        else:
            results.append(RuleResult("team_advertising", "Team Advertising Compliance", "pass",
                "Team name with broker affiliation found."))
    else:
        results.append(RuleResult("team_advertising", "Team Advertising Compliance", "skip",
            "No team name detected — check skipped."))

    # 5. AB 723 — altered image disclosure
    has_photos = bool(re.search(r'<img ', html, re.I))
    if not has_photos:
        results.append(RuleResult("ab723_images", "AB 723 – Altered Image Disclosure", "skip",
            "No images detected on page."))
    elif AB723_RE.search(text):
        results.append(RuleResult("ab723_images", "AB 723 – Altered Image Disclosure", "pass",
            "Digitally altered image disclosure language found.",
            source_url="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202320240AB723"))
    else:
        results.append(RuleResult("ab723_images", "AB 723 – Altered Image Disclosure", "warn",
            "Property images found but no AB 723 disclosure detected.",
            detail="California AB 723 requires disclosure if listing images are virtually staged or digitally altered.",
            fix="Add a disclosure near property photos: 'Photo(s) may be virtually staged or digitally enhanced.'",
            source_url="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202320240AB723"))

    # 6. Virtual office rules
    if VIRTUAL_OFFICE_RE.search(text):
        if DRE_LICENSE_RE.search(text):
            results.append(RuleResult("virtual_office", "Virtual Office Advertisement Rules", "pass",
                "VOA with DRE license — appears compliant.",
                source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html"))
        else:
            results.append(RuleResult("virtual_office", "Virtual Office Advertisement Rules", "fail",
                "Virtual office reference without DRE license number.",
                fix="Virtual office advertisements require your DRE license number.",
                source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html"))

    # 7. CCPA privacy policy
    if CCPA_RE.search(text):
        results.append(RuleResult("ccpa_privacy", "CCPA Privacy Policy", "pass",
            "Privacy policy link or CCPA language found.",
            source_url="https://oag.ca.gov/privacy/ccpa"))
    else:
        results.append(RuleResult("ccpa_privacy", "CCPA Privacy Policy", "fail",
            "No privacy policy detected.",
            fix="Add a Privacy Policy page and link it in your footer. Include CCPA-required disclosures.",
            source_url="https://oag.ca.gov/privacy/ccpa"))

    # 8. Contact info
    has_phone = bool(re.search(r'\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}', text))
    has_email = bool(re.search(r'[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}', text, re.I))
    if has_phone or has_email:
        results.append(RuleResult("contact_info", "Contact Information", "pass",
            "Phone or email contact found on page."))
    else:
        results.append(RuleResult("contact_info", "Contact Information", "warn",
            "No phone number or email address detected.",
            fix="Ensure your contact information is visible on the page."))

    return results


def run_lending_checks(text: str, html: str) -> list[RuleResult]:
    results = []

    # 1. TILA / Reg Z APR proximity
    results.append(check_tila_proximity(text))

    # 2. NMLS number
    if NMLS_RE.search(text):
        results.append(RuleResult("safe_nmls", "SAFE Act – NMLS Number", "pass",
            "NMLS number found on page.",
            source_url="https://mortgage.nationwidelicensingsystem.org/"))
    else:
        results.append(RuleResult("safe_nmls", "SAFE Act – NMLS Number", "fail",
            "No NMLS number detected.",
            fix="Add your NMLS ID (e.g. 'NMLS #276626') to your footer on every page.",
            source_url="https://mortgage.nationwidelicensingsystem.org/"))

    # 3. Equal Housing Lender
    has_lender = bool(re.search(r'equal\s+housing\s+lender', text, re.I))
    has_img    = bool(EHO_IMG_RE.search(html))
    if has_lender or has_img:
        results.append(RuleResult("equal_housing_lender", "Equal Housing Lender", "pass",
            "Equal Housing Lender statement or logo found.",
            source_url="https://www.consumerfinance.gov/rules-policy/regulations/1002/"))
    else:
        results.append(RuleResult("equal_housing_lender", "Equal Housing Lender", "fail",
            "Equal Housing Lender statement or logo not found.",
            fix="Display 'Equal Housing Lender' and the Equal Housing logo on your site.",
            source_url="https://www.consumerfinance.gov/rules-policy/regulations/1002/"))

    # 4. CCPA
    if CCPA_RE.search(text):
        results.append(RuleResult("ccpa_privacy", "CCPA Privacy Policy", "pass",
            "Privacy policy found.",
            source_url="https://oag.ca.gov/privacy/ccpa"))
    else:
        results.append(RuleResult("ccpa_privacy", "CCPA Privacy Policy", "fail",
            "No privacy policy detected.",
            fix="Add a Privacy Policy with CCPA disclosures.",
            source_url="https://oag.ca.gov/privacy/ccpa"))

    # 5. DFPI prohibited claims
    prohibited = re.compile(
        r'\b(guaranteed\s+approv|no\s+credit\s+check|instant\s+approv|always\s+approv|100%\s+financ)\b', re.I
    )
    hit = prohibited.search(text)
    if hit:
        results.append(RuleResult("dfpi_prohibited", "DFPI – Prohibited Claims", "fail",
            "Potentially prohibited claim detected.",
            detail=f"Found: '{hit.group()[:80]}'",
            fix="Remove or rewrite claims like 'guaranteed approval' or 'no credit check' — these violate DFPI advertising rules.",
            source_url="https://dfpi.ca.gov/"))
    else:
        results.append(RuleResult("dfpi_prohibited", "DFPI – Prohibited Claims", "pass",
            "No prohibited DFPI advertising claims detected.",
            source_url="https://dfpi.ca.gov/"))

    # 6. ADA accessibility signal
    if ADA_RE.search(text):
        results.append(RuleResult("ada_accessibility", "ADA Accessibility Statement", "pass",
            "Accessibility statement or compliance language found."))
    else:
        results.append(RuleResult("ada_accessibility", "ADA Accessibility Statement", "warn",
            "No accessibility statement detected.",
            fix="Add an accessibility statement and consider an ADA compliance review."))

    return results


def score_results(results: list[RuleResult]) -> int:
    """Calculate 0-100 compliance score."""
    scorable = [r for r in results if r.status != "skip"]
    if not scorable:
        return 100
    points = sum({"pass": 10, "warn": 5, "fail": 0}.get(r.status, 5) for r in scorable)
    return round((points / (len(scorable) * 10)) * 100)


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

    scan_id = req.scan_id
    await update_scan_status(scan_id, "running")

    t0 = time.time()
    try:
        scraped = await scrape_website(url)
        text = scraped["text"]
        html = scraped["raw_html"]

        if req.profession == "lending":
            rule_results = run_lending_checks(text, html)
        else:
            rule_results = run_realestate_checks(text, html)

        score = score_results(rule_results)
        elapsed = round(time.time() - t0, 1)

        # Strip fix instructions for free scans (gated by plan on frontend too)
        is_free = reason == "free"

        response = {
            "scan_id": scan_id or str(uuid.uuid4()),
            "score": score,
            "url": scraped["url_final"],
            "profession": req.profession,
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
                    "fix": None if is_free else r.fix,   # locked for free users
                }
                for r in rule_results
            ]
        }

        await update_scan_status(scan_id, "completed", result=response)

        # Record fingerprint for free scans
        if is_free and scan_id:
            await record_fingerprint(ip, req.email, scan_id)

        return response

    except Exception as exc:
        error_type = classify_error(exc)
        error_msg  = ERROR_MESSAGES[error_type]
        log.error(f"Scan failed [{error_type}] {url}: {exc}")
        await update_scan_status(scan_id, "failed", error_type=error_type.value, error_message=error_msg)
        raise HTTPException(status_code=422, detail={"error_type": error_type.value, "message": error_msg})


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
