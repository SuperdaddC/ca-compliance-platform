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
            await client.post(
                f"{SUPABASE_URL}/rest/v1/scans",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal"
                },
                json=payload
            )
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
DRE_LICENSE_RE = re.compile(r'\bdre\s*#?\s*\d{7,9}\b|\bcalifornia\s+real\s+estate\s+broker\s+#?\s*\d{7,9}\b|\bcalbre\s*#?\s*\d{7,9}\b', re.I)
BROKER_DRE_RE  = re.compile(r'\b(broker|brokerage)\s*.{0,30}dre\s*#?\s*\d{7,9}\b', re.I)
NMLS_RE        = re.compile(r'\bnmls\s*#?\s*\d{4,10}\b', re.I)
EQUAL_HOUSING_RE = re.compile(r'equal\s+housing\s+(opportunity|lender|logo)', re.I)
EHO_IMG_RE     = re.compile(r'(equal.housing|eho[\-_]?logo|fair.housing)', re.I)
CCPA_RE        = re.compile(r'privacy\s+policy|ccpa|do\s+not\s+sell', re.I)
DO_NOT_SELL_RE = re.compile(r'do\s+not\s+sell(\s+or\s+share)?\s+(my|personal)', re.I)
ADA_RE         = re.compile(r'accessibility|ada\s+complian|wcag', re.I)
PHYSICAL_ADDR_RE = re.compile(r'\b\d{2,5}\s+[A-Z][a-z]+.*?(ave|st|blvd|dr|rd|ln|ct|way|pkwy|pl|cir)\b', re.I)

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
def run_realestate_checks(text: str, html: str) -> list[RuleResult]:
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
    if RESPONSIBLE_BROKER_RE.search(text) or BROKER_DRE_RE.search(text):
        results.append(RuleResult("responsible_broker", "Responsible Broker Disclosure", "pass",
            "Responsible broker disclosure found.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="California Business & Professions Code §10159.5 — 'Every licensed salesperson shall have and be under a written agreement with the responsible broker.' Commissioner's Regulation §2773.1 — All advertising by or on behalf of a salesperson must include the identity of the responsible broker or the name of the employing brokerage firm."))
    else:
        results.append(RuleResult("responsible_broker", "Responsible Broker Disclosure", "fail",
            "No responsible or supervising broker identified on this page.",
            detail="If you are a salesperson (not a broker), your website must identify your supervising/responsible broker and their DRE license number.",
            source_url="https://www.dre.ca.gov/Licensees/AdvertisingGuidelines.html",
            regulation="California Business & Professions Code §10159.5 — Salesperson advertising must include broker identity. Commissioner's Regulation §2773.1 — 'The name of the broker must appear in advertising in a manner that is at least as prominent as the name of the salesperson.' The broker's name and DRE number must be reasonably prominent.",
            fix="Add to your site footer: '[Your Name], DRE #[Your Number] | [Brokerage Name], DRE #[Broker Number]'. If you are a broker, this check may be a false positive — ensure your broker license number is displayed.",
            webmaster_email=WM_RESPONSIBLE_BROKER))

    # 3. Equal Housing Opportunity
    has_text = bool(EQUAL_HOUSING_RE.search(text))
    has_img  = bool(EHO_IMG_RE.search(html))
    if has_text or has_img:
        results.append(RuleResult("equal_housing", "Equal Housing Opportunity", "pass",
            "Equal Housing Opportunity logo or statement found.",
            source_url="https://www.hud.gov/program_offices/fair_housing_equal_opp/advertising_and_marketing",
            regulation="Fair Housing Act (42 U.S.C. §3604(c)) — It is unlawful 'to make, print, or publish any notice, statement, or advertisement with respect to the sale or rental of a dwelling that indicates any preference, limitation, or discrimination based on race, color, religion, sex, handicap, familial status, or national origin.' HUD Advertising Guidelines (24 CFR Part 109) require the Equal Housing Opportunity logo or statement in all real estate advertising."))
    else:
        results.append(RuleResult("equal_housing", "Equal Housing Opportunity", "fail",
            "Equal Housing Opportunity logo or statement not found.",
            detail="The Fair Housing Act and HUD advertising guidelines require all real estate advertising to include the Equal Housing Opportunity logo and/or the statement 'Equal Housing Opportunity.'",
            source_url="https://www.hud.gov/program_offices/fair_housing_equal_opp/advertising_and_marketing",
            regulation="Fair Housing Act (42 U.S.C. §3604(c)) — Prohibits discriminatory advertising. HUD Advertising Guidelines (24 CFR Part 109.30) — 'All advertising of residential real estate for sale, rent, or financing should contain an equal housing opportunity logotype, statement, or slogan.' The logo must be of a size 'at least equal to the largest of other logotypes.'",
            fix="Add the Equal Housing Opportunity logo and the words 'Equal Housing Opportunity' to your website footer. Download the official HUD logo from hud.gov. The logo should be clearly visible — not hidden or miniaturized.",
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
    has_privacy = CCPA_RE.search(text) or \
                  bool(re.search(r'(href|link|url).*?privacy[\-_\s]?policy|privacy[\-_\s]?policy.*?(href|link|url)', html, re.I)) or \
                  bool(re.search(r'href=["\'][^"\']*privacy[^"\']*["\']', html, re.I))
    has_dns     = DO_NOT_SELL_RE.search(text) or \
                  bool(re.search(r'do[\-_\s]*not[\-_\s]*sell', html, re.I))
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
    has_phone = bool(re.search(r'\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}', text))
    # Check for email in text AND in mailto: links in the raw HTML
    has_email = bool(re.search(r'[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}', text, re.I)) or \
                bool(re.search(r'mailto:[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}', html, re.I)) or \
                bool(re.search(r'(click\s+(here\s+)?to\s+)?e[\-\s]?mail\s+(me|us)|contact\s+us|send\s+(a\s+)?message|get\s+in\s+touch|reach\s+(out|us)', text, re.I)) or \
                bool(re.search(r'href=["\'][^"\']*(/contact|/email|/reach-out|/get-in-touch|/message)[^"\']*["\']', html, re.I))
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
    if ADA_RE.search(text):
        results.append(RuleResult("ada_accessibility", "ADA Accessibility Statement", "pass",
            "Accessibility statement or compliance language found.",
            source_url="https://www.ada.gov/resources/web-guidance/",
            regulation="Americans with Disabilities Act, Title III (42 U.S.C. §12182) — Public accommodations, which courts have interpreted to include websites, must be accessible to individuals with disabilities. DOJ guidance (March 2022) confirms websites must be accessible."))
    else:
        results.append(RuleResult("ada_accessibility", "ADA Accessibility Statement", "warn",
            "No accessibility statement detected on this page.",
            detail="While not a DRE requirement, the ADA requires websites of public accommodations to be accessible. NAR recommends all REALTOR websites include an accessibility statement.",
            source_url="https://www.ada.gov/resources/web-guidance/",
            regulation="ADA Title III (42 U.S.C. §12182) — Websites of public accommodations must be accessible. NAR Accessibility Best Practices (2023) recommend an accessibility statement and WCAG 2.1 AA conformance.",
            fix="Add an accessibility statement page linked from your footer. State your commitment to accessibility and provide a way for users to report issues. Consider a WCAG 2.1 AA audit of your site."))

    return results


# ---------------------------------------------------------------------------
# Lending / MLO checks
# ---------------------------------------------------------------------------
def run_lending_checks(text: str, html: str) -> list[RuleResult]:
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
    has_lender = bool(re.search(r'equal\s+housing\s+lender', text, re.I))
    has_img    = bool(EHO_IMG_RE.search(html))
    if has_lender or has_img:
        results.append(RuleResult("equal_housing_lender", "Equal Housing Lender Statement", "pass",
            "Equal Housing Lender statement or logo found.",
            source_url="https://www.consumerfinance.gov/rules-policy/regulations/1002/4/",
            regulation="Regulation B (12 CFR §1002.4(b)), implementing the Equal Credit Opportunity Act (15 U.S.C. §1691) — 'A creditor that advertises credit shall include in each advertisement a statement of the creditor's compliance with the Equal Credit Opportunity Act.' For mortgage lenders, this means displaying 'Equal Housing Lender' and the Equal Housing logo."))
    else:
        results.append(RuleResult("equal_housing_lender", "Equal Housing Lender Statement", "fail",
            "'Equal Housing Lender' statement or logo not found on this page.",
            detail="Mortgage lender advertising must include 'Equal Housing Lender' (not just 'Equal Housing Opportunity'). This is a separate requirement under Regulation B / ECOA.",
            source_url="https://www.consumerfinance.gov/rules-policy/regulations/1002/4/",
            regulation="Regulation B (12 CFR §1002.4(b)) — 'A creditor shall provide the appropriate notice to an applicant.' For advertising, creditors must include: 'Equal Housing Lender' or the Equal Housing Lender logo. The Federal Reserve Board's Official Staff Commentary confirms this applies to all forms of advertising, including internet advertising.",
            fix="Display the words 'Equal Housing Lender' and the Equal Housing Lender logo in your website footer. Note: 'Equal Housing Opportunity' alone is not sufficient for mortgage lenders — you must specifically use 'Equal Housing Lender.'",
            webmaster_email=WM_EHL))

    # 5. CCPA privacy policy
    has_privacy = CCPA_RE.search(text)
    has_dns     = DO_NOT_SELL_RE.search(text)
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

    # 7. Contact information
    has_phone = bool(re.search(r'\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}', text))
    has_email = bool(re.search(r'[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}', text, re.I))
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
    if ADA_RE.search(text):
        results.append(RuleResult("ada_accessibility", "ADA Accessibility Statement", "pass",
            "Accessibility statement or compliance language found.",
            source_url="https://www.ada.gov/resources/web-guidance/",
            regulation="ADA Title III (42 U.S.C. §12182) — Websites of public accommodations must be accessible to individuals with disabilities."))
    else:
        results.append(RuleResult("ada_accessibility", "ADA Accessibility Statement", "warn",
            "No accessibility statement detected.",
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

        if req.profession == "lending":
            rule_results = run_lending_checks(text, html)
        else:
            rule_results = run_realestate_checks(text, html)

        # --- Shared checks (both professions) ---
        ai_check = await check_ai_crawler_blocking(scraped["url_final"])
        rule_results.append(ai_check)

        score = score_results(rule_results)
        elapsed = round(time.time() - t0, 1)

        response = {
            "scan_id": scan_id,
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
                    "fix": r.fix,
                    "regulation": r.regulation,
                    "webmaster_email": r.webmaster_email,
                }
                for r in rule_results
            ]
        }

        await update_scan_status(scan_id, "completed", result=response)

        # Record fingerprint for free scans
        if is_free and scan_id:
            await record_fingerprint(ip, req.email, scan_id)

        # Send email notification (fire-and-forget)
        await send_scan_email(req.email, response["scan_id"], response, is_paid=not is_free)

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
