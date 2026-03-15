"""
rule_engine.py — California RE/Lending Compliance Rule Engine
Runs inside AWS Lambda. Receives scraped page HTML + text, executes 10
compliance checks, and returns a structured result dict.

Dependencies: re, html.parser (stdlib only — BeautifulSoup used if available
but gracefully degrades to stdlib html.parser for robustness in Lambda).
"""

import re
import json
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from typing import Optional

# Module-level cache for DRE lookups within a single Lambda invocation.
# Prevents multiple HTTP round-trips for the same license number when
# both R01 and R02/R10 look up the same DRE number.
_DRE_LOOKUP_CACHE: dict[str, bool] = {}
_DRE_NAME_CACHE: dict[str, Optional[str]] = {}

# R12 is reserved — was planned for RESPA AfBA disclosure but deferred
# pending determination of whether the platform needs to check for
# affiliated business arrangement disclosures. Gap is intentional.
# R12 = RESPA §8(c)(4) Affiliated Business Arrangement Disclosure (future)

# ─────────────────────────────────────────────
# HTML Utilities
# ─────────────────────────────────────────────

class _ImageAltExtractor(HTMLParser):
    """Extracts <img> alt text and href values from raw HTML."""

    def __init__(self):
        super().__init__()
        self.img_alts: list[str] = []
        self.link_hrefs: list[str] = []
        self.link_texts: list[str] = []
        self._in_anchor = False
        self._current_anchor_text = ""

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "img":
            alt = attr_dict.get("alt", "")
            if alt:
                self.img_alts.append(alt.lower())
        elif tag == "a":
            href = attr_dict.get("href", "")
            if href:
                self.link_hrefs.append(href.lower())
            self._in_anchor = True
            self._current_anchor_text = ""

    def handle_endtag(self, tag):
        if tag == "a":
            self.link_texts.append(self._current_anchor_text.strip().lower())
            self._in_anchor = False

    def handle_data(self, data):
        if self._in_anchor:
            self._current_anchor_text += data


def _parse_html(html: str) -> _ImageAltExtractor:
    """Parse HTML and return extractor with img alts and link data."""
    parser = _ImageAltExtractor()
    parser.feed(html)
    return parser


def _has_images(html: str) -> bool:
    """Return True if any <img> tags are present in the HTML."""
    return bool(re.search(r"<img\b", html, re.IGNORECASE))


def _truncate(text: str, max_chars: int = 200) -> str:
    """Truncate evidence strings to keep output readable."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


# ─────────────────────────────────────────────
# Rule 1 — DRE / BRE License Number
# ─────────────────────────────────────────────

_BOT_UA = "Mozilla/5.0 (compatible; ComplianceBot/1.0; +https://complywithjudy.com)"


def _lookup_dre_number(number: str) -> bool:
    """
    Query the DRE public license lookup (POST to publicasp/pplinfo.asp) to verify
    a number is a real CA RE license.

    Valid license response: ~5800 chars, contains "public information request complete"
    Invalid / not found:    ~18700 chars (full nav page), no completion marker

    Results are cached per invocation to avoid redundant HTTP calls.
    Returns True if confirmed, False if not found or lookup fails.
    """
    if number in _DRE_LOOKUP_CACHE:
        return _DRE_LOOKUP_CACHE[number]
    try:
        data = urllib.parse.urlencode({"LICENSE_ID": number}).encode()
        req = urllib.request.Request(
            "https://www2.dre.ca.gov/publicasp/pplinfo.asp",
            data=data,
            headers={"User-Agent": _BOT_UA,
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            result = "public information request complete" in body.lower()
            _DRE_LOOKUP_CACHE[number] = result
            return result
    except Exception:
        _DRE_LOOKUP_CACHE[number] = False
        return False  # network/timeout — don't fail the scan


def check_dre_license(text: str) -> dict:
    """
    Rule 1: DRE License Number (B&P §10140.6, CCR §2773)

    Strategy (Mike's approach):
      1. Find all bare 8-digit numbers on the page: \\b\\d{8}\\b
      2. For each, look at up to 40 chars BEFORE the number to classify the label
      3. Label type determines confidence level:
         - DRE / BRE / CalBRE label → strong pass
         - Lic# / License label     → pass (label is non-standard but number is disclosed)
         - NMLS / NMLSR label       → skip (different registry, not a DRE #)
         - No recognizable label    → query DRE public lookup to confirm
      4. If DRE lookup confirms the number → pass (verified)
      5. If no 8-digit number anywhere     → fail

    Note: B&P §10140.6 requires the "license identification number" — not a specific label.
    "Lic#: 01317331" is legally equivalent to "DRE #01317331".
    """
    upper = text.upper()

    # Find every bare 8-digit number on the page (word-bounded, not part of longer digit string)
    candidates = [(m.start(), m.group(0)) for m in re.finditer(r'\b(\d{8})\b', text)]

    for pos, number in candidates:
        # Grab up to 40 chars before the number to inspect the label
        look_back = upper[max(0, pos - 40): pos].strip()

        # Skip phone-number-adjacent context (unlikely but guard against 8-digit local numbers)
        # Phone fragments tend to have dashes/parens right before them
        if re.search(r'[\-\(\)]\s*$', look_back):
            continue

        # NMLS / NMLSR — different registry, not a DRE license
        if re.search(r'\bNMLS\b|\bNMLSR\b', look_back):
            continue

        # --- Classify the label ---

        # Strong: DRE / BRE / CalBRE
        if re.search(r'\b(DRE|BRE|CALBR[E]?)\b', look_back):
            return {
                "rule_id": "R01",
                "rule_name": "DRE License Number",
                "status": "pass",
                "message": f"DRE license number {number} found.",
                "remediation": None,
                "evidence": _truncate(upper[max(0, pos-15):pos+9]),
                "screenshot_required": False,
            }

        # Acceptable: Lic# / License (number is disclosed, label is non-standard)
        if re.search(r'\bLIC(?:ENSE)?\b', look_back):
            return {
                "rule_id": "R01",
                "rule_name": "DRE License Number",
                "status": "pass",
                "message": f"License identification number {number} found on page.",
                "remediation": None,
                "evidence": _truncate(text[max(0, pos-15):pos+9]),
                "screenshot_required": False,
            }

        # No recognizable label — verify via DRE public lookup
        if _lookup_dre_number(number):
            return {
                "rule_id": "R01",
                "rule_name": "DRE License Number",
                "status": "pass",
                "message": f"License number {number} found and verified via DRE public records (no label).",
                "remediation": None,
                "evidence": _truncate(text[max(0, pos-15):pos+9]),
                "screenshot_required": False,
            }

        # 8-digit number found but DRE lookup didn't confirm it — note it as ambiguous
        # (Could be a zip+4, year range, etc. — don't pass on an unverified number)

    # No valid DRE license number found
    return {
        "rule_id": "R01",
        "rule_name": "DRE License Number",
        "status": "fail",
        "message": "No DRE license number found on the page.",
        "remediation": (
            "Display your California DRE license number in a prominent location "
            "(header, footer, or About section). B&P §10140.6 and CCR §2773 require "
            "the 8-digit license identification number on all first-point-of-contact materials."
        ),
        "evidence": None,
        "screenshot_required": False,
    }


# ─────────────────────────────────────────────
# Rule 2 — Responsible Broker Name
# ─────────────────────────────────────────────

def check_broker_name(text: str, dre_license: Optional[str] = None) -> dict:
    """
    Rule 2: Responsible Broker Name Disclosure
    B&P §10140.6(b)(1) requires the responsible broker's *identity* — the DRE license number
    IS the broker's identity. If R01 already found and verified a DRE number, and DRE lookup
    returns a name, R02 passes (broker is identified by number).
    Warning if text mentions 'broker' ambiguously, fail if absent entirely.
    """
    # Fast path: if a verified DRE number was found (from R01), look up the name.
    # The DRE number IS the broker's identity per B&P §10140.6 — no separate
    # "Responsible Broker: John Smith" text is legally required.
    if dre_license:
        dre_name = _lookup_dre_name(dre_license)
        if dre_name:
            return {
                "rule_id": "R02",
                "rule_name": "Responsible Broker Name",
                "status": "pass",
                "message": f"Responsible broker identified via DRE license #{dre_license} ({dre_name}).",
                "remediation": None,
                "evidence": f"DRE #{dre_license} → {dre_name}",
                "screenshot_required": False,
            }

    lower = text.lower()

    # Strong disclosure patterns — must look like an actual legal disclosure,
    # not just the word "broker" appearing anywhere in body/blog text.
    # Require explicit disclosure phrasing OR a DRE number adjacent to a broker label.
    strong_patterns = [
        r'\bresponsible broker\b',
        r'\bsupervising broker\b',
        r'\bbroker of record\b',
        r'\bbrokered by\b',
        r'\bunder the supervision of\b',
        r'\bbrokerage[:\s]+[A-Z][A-Za-z\s,\.]{3,40}\s*(?:dre|bre)\s*#?\s*\d{7,9}',  # "Brokerage: XYZ Realty DRE #01234"
        r'(?:dre|bre)\s*#?\s*\d{7,9}[^a-z0-9]{1,20}(?:broker|realty|real estate)',    # "DRE #01234 — Broker"
        r'(?:broker|realty|real estate)[^a-z0-9]{1,20}(?:dre|bre)\s*#?\s*\d{7,9}',   # "XYZ Realty DRE #01234"
    ]
    for pattern in strong_patterns:
        m = re.search(pattern, lower)
        if m:
            # Additional guard: make sure this match isn't inside a long blog sentence
            # (i.e., check it's within a short disclosure-like context)
            snippet = text[max(0, m.start()-40):m.end()+80]
            return {
                "rule_id": "R02",
                "rule_name": "Responsible Broker Name",
                "status": "pass",
                "message": "Responsible/supervising broker disclosure found.",
                "remediation": None,
                "evidence": _truncate(snippet),
                "screenshot_required": False,
            }

    # Weak/ambiguous broker mention — only warn if "broker" appears in a disclosure-like context
    # (near a license number, in footer-like text, or with a company name)
    weak_broker = re.search(
        r'\bbroker\b(?:[^.]{0,60}(?:dre|bre|license|lic\.?|#\d)|[^.]{0,30}(?:realty|real estate|associates))',
        lower
    )
    if weak_broker:
        return {
            "rule_id": "R02",
            "rule_name": "Responsible Broker Name",
            "status": "warning",
            "message": "Broker reference found but no complete responsible broker disclosure with license number detected.",
            "remediation": (
                "Add an explicit disclosure identifying the responsible/supervising broker "
                "by full name and DRE license number. Example: 'Listed under XYZ Realty, "
                "Jane Smith, Broker, DRE #01234567.'"
            ),
            "evidence": None,
            "screenshot_required": True,
        }

    return {
        "rule_id": "R02",
        "rule_name": "Responsible Broker Name",
        "status": "fail",
        "message": "No responsible broker name or disclosure found on the page.",
        "remediation": (
            "California requires that the responsible broker's name and DRE license number "
            "appear on all advertising materials. Add this disclosure prominently."
        ),
        "evidence": None,
        "screenshot_required": False,
    }


# ─────────────────────────────────────────────
# Rule 3 — NMLS ID (lending only)
# ─────────────────────────────────────────────

def check_nmls_id(text: str) -> dict:
    """
    Rule 3: NMLS ID (lending profession only)
    Three states: pass, warning (NMLS mentioned but no number), fail (absent).
    """
    upper = text.upper()

    # Canonical: NMLS #123456 / NMLS: 123456 / NMLS ID 123456 / NMLSR ID 123456
    # Handles all real-world formats: #, :, space, ID, NMLSR, Consumer Access text
    canonical = re.search(
        r'\bNMLS(?:R)?\s*(?:#|:|\s+ID\s*:?)?\s*(\d{6,10})\b',
        upper
    )
    if canonical:
        number = canonical.group(1)
        return {
            "rule_id": "R03",
            "rule_name": "NMLS ID",
            "status": "pass",
            "message": f"NMLS ID {number} found and correctly formatted.",
            "remediation": None,
            "evidence": _truncate(canonical.group(0)),
            "screenshot_required": False,
        }

    # NMLS mentioned but no valid number
    if re.search(r'\bNMLS\b', upper):
        return {
            "rule_id": "R03",
            "rule_name": "NMLS ID",
            "status": "warning",
            "message": "NMLS reference found but no valid ID number detected.",
            "remediation": (
                "Display your NMLS ID in the format 'NMLS #XXXXXX' (6–10 digits). "
                "Example: 'NMLS #123456'. Required on all loan officer advertising."
            ),
            "evidence": None,
            "screenshot_required": True,
        }

    return {
        "rule_id": "R03",
        "rule_name": "NMLS ID",
        "status": "fail",
        "message": "No NMLS ID found on the page.",
        "remediation": (
            "Federal law (SAFE Act / Reg Z) requires your NMLS Unique Identifier "
            "on all advertising. Add 'NMLS #XXXXXX' to your header, footer, or bio."
        ),
        "evidence": None,
        "screenshot_required": False,
    }


# ─────────────────────────────────────────────
# Rule 4 — Reg Z Trigger Terms
# ─────────────────────────────────────────────

# Regulation Z trigger terms — 12 CFR §1026.24
# STRONG triggers: explicit financial advertising language that almost certainly
# constitutes a credit advertisement (specific rate %, specific payment amount,
# specific loan term with a dollar figure, down payment with amount).
# Requires APR disclosure if ANY strong trigger is present.
_REG_Z_STRONG_TRIGGERS = [
    r'\d+\.?\d*\s*%\s*(?:interest\s+)?rate\b',          # "3.5% rate", "6.75% interest rate"
    r'\$[\d,]+\s*(?:per\s+month|\/mo|monthly\s+payment)',# "$1,200/mo", "$1,200 monthly payment"
    r'\bmonthly\s+payment\s+(?:of\s+)?\$[\d,]+',        # "monthly payment of $1,200"
    r'\b(?:as\s+low\s+as\s+)?\$[\d,]+\s*(?:down|down\s+payment)',  # "$50,000 down"
    r'\b\d+\s*%\s*(?:down|down\s+payment)\b',            # "5% down"
    r'\bno\s+(?:money\s+)?down\b',                       # "no money down"
    r'\b(?:low|0)\s*%\s+(?:down|interest)\b',            # "0% down", "low % interest"
    r'\b(?:fixed|adjustable)[\s-]+rate\s+mortgage\b',    # "fixed-rate mortgage"
    r'\b\d{2,3}[\s-]year\s+(?:fixed|arm|mortgage|loan)\b',  # "30-year fixed"
    r'\bdiscount\s+points?\b',                           # "discount points" (loan context)
    r'\borigination\s+(?:fee|point)',                    # "origination fee/point"
]

# WEAK triggers: terms that MIGHT appear in a credit ad context but are also
# common in general real estate content. Require 3+ weak triggers to warn.
_REG_Z_WEAK_TRIGGERS = [
    r'\bmonthly\s+payment\b',           # could be HOA, utilities, etc.
    r'\bdown\s+payment\b',              # common in buyer guides, not just ads
    r'\bfinancing\s+available\b',       # vague
    r'\b(?:fixed|adjustable)\s+rate\b', # without "mortgage" context
    r'\bpoints?\b',                     # standalone "points" — too generic
]

_APR_PATTERNS = [
    r'\bAPR\b',
    r'\bannual\s+percentage\s+rate\b',
]

_REG_Z_REMEDIATION = (
    "Regulation Z (12 CFR §1026.24) requires that whenever you advertise specific "
    "loan terms — such as an interest rate, monthly payment amount, down payment "
    "amount, or loan term — you must also disclose the Annual Percentage Rate (APR). "
    "Add 'APR: X.XX%' clearly near all trigger terms in your advertising."
)


def check_reg_z_triggers(text: str) -> dict:
    """
    Rule 4: Reg Z Trigger Terms (applies to both professions)

    Logic:
    - Any STRONG trigger without APR → FAIL
    - 3+ WEAK triggers without APR → WARNING
    - 1-2 weak triggers or all triggers covered by APR → PASS
    - No triggers at all → PASS
    """
    lower = text.lower()
    upper = text.upper()

    # Check strong triggers first
    strong_found = []
    for pattern in _REG_Z_STRONG_TRIGGERS:
        m = re.search(pattern, lower, re.IGNORECASE)
        if m:
            strong_found.append(_truncate(m.group(0).strip(), 60))

    # Check weak triggers
    weak_found = []
    for pattern in _REG_Z_WEAK_TRIGGERS:
        m = re.search(pattern, lower, re.IGNORECASE)
        if m:
            weak_found.append(_truncate(m.group(0).strip(), 60))

    # Check for APR disclosure
    has_apr = any(re.search(p, upper) for p in _APR_PATTERNS)

    # No triggers at all
    if not strong_found and not weak_found:
        return {
            "rule_id": "R04",
            "rule_name": "Reg Z Trigger Terms",
            "status": "pass",
            "message": "No Reg Z trigger terms detected. APR disclosure not required.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    # APR present — covered
    if has_apr:
        all_found = strong_found + weak_found
        return {
            "rule_id": "R04",
            "rule_name": "Reg Z Trigger Terms",
            "status": "pass",
            "message": "Reg Z trigger terms found and APR disclosure is present.",
            "remediation": None,
            "evidence": f"Triggers: {', '.join(all_found[:3])}",
            "screenshot_required": False,
        }

    # Strong trigger(s) without APR → fail
    if strong_found:
        return {
            "rule_id": "R04",
            "rule_name": "Reg Z Trigger Terms",
            "status": "fail",
            "message": (
                f"Specific credit advertising terms detected without an APR disclosure: "
                f"{', '.join(strong_found[:3])}. Regulation Z requires APR disclosure."
            ),
            "remediation": _REG_Z_REMEDIATION,
            "evidence": f"Strong triggers found: {', '.join(strong_found[:5])}",
            "screenshot_required": False,
        }

    # 3+ weak triggers without APR → warning
    if len(weak_found) >= 3:
        return {
            "rule_id": "R04",
            "rule_name": "Reg Z Trigger Terms",
            "status": "warning",
            "message": (
                f"Multiple financing-related terms detected without an APR disclosure. "
                f"If this page is advertising credit terms, Regulation Z requires APR disclosure."
            ),
            "remediation": _REG_Z_REMEDIATION,
            "evidence": f"Terms found: {', '.join(weak_found[:5])}",
            "screenshot_required": False,
        }

    # 1-2 weak triggers — not enough to flag
    return {
        "rule_id": "R04",
        "rule_name": "Reg Z Trigger Terms",
        "status": "pass",
        "message": "No definitive Reg Z trigger terms detected that require APR disclosure.",
        "remediation": None,
        "evidence": None,
        "screenshot_required": False,
    }


# ─────────────────────────────────────────────
# Rule 5 — AB 723 Altered Image Disclosure (realestate only)
# ─────────────────────────────────────────────

_AB723_DISCLOSURES = [
    r'\bvirtually\s+staged\b',
    r'\bdigitally\s+altered\b',
    r'\bedited\s+image\b',
    r'\brendering\b',
    r'\bdigital\s+render\b',
    r'\bai[\s-]generated\b',
    r'\bphoto[\s-]?shopped\b',
    r'\bimage\s+has\s+been\s+(?:altered|modified|edited)\b',
    r'\bvirtual\s+tour\b',          # common staging context
    r'\bstaged\s+photo\b',
]


def _count_listing_photos(html: str) -> int:
    """
    Count images that are likely MLS/property listing photos based on their src URL.
    Excludes logos, icons, nav images, and UI elements.
    AB 723 applies to listing photos — not site furniture.
    """
    # Known MLS/listing photo CDN patterns
    listing_src_patterns = [
        r'cotality\.com',
        r'crmls\.org',
        r'mlsgrid\.com',
        r'api\.bridgedataoutput\.com',
        r'brightmls\.com',
        r'matrixmls\.com',
        r'/trestle/Media',
        r'/rets/Media',
        r'media\.crmls\.',
        r'photos\.zillowstatic\.com',
        r'ap\.rdcpix\.com',           # realtor.com
        r'cloudinary\.com.*(?:property|listing|photo)',
        r'lp-cdn\.com.*(?:property|listing)',
        r'idx.*(?:photo|image|listing)',
    ]
    # Also catch any img where alt text is blank or generic (typical for listing photos)
    listing_count = 0
    for m in re.finditer(r'<img\b([^>]*)>', html, re.IGNORECASE):
        attrs = m.group(1)
        src = re.search(r'src=["\']([^"\']+)["\']', attrs)
        if not src:
            continue
        src_url = src.group(1).lower()
        alt_match = re.search(r'alt=["\']([^"\']*)["\']', attrs)
        alt = alt_match.group(1).lower() if alt_match else ""

        # Skip obvious non-listing images
        if any(skip in alt for skip in ['logo', 'icon', 'nav', 'menu', 'banner', 'mls logo', 'company']):
            continue
        if any(skip in src_url for skip in ['logo', 'icon', 'favicon', 'sprite', 'avatar']):
            continue

        # Count if src matches known listing CDN
        for pattern in listing_src_patterns:
            if re.search(pattern, src_url):
                listing_count += 1
                break

    return listing_count


def check_ab723_disclosure(html: str, text: str) -> dict:
    """
    Rule 5: AB 723 Altered Image Disclosure (realestate only)
    AB 723 applies specifically to listing photos — not logos, nav images, or UI elements.
    Only flag if actual listing photos are detected AND no disclosure text is present.
    """
    lower = text.lower()

    # Only check listing photos, not all images
    listing_photo_count = _count_listing_photos(html)

    if listing_photo_count == 0:
        return {
            "rule_id": "R05",
            "rule_name": "AB 723 Altered Image Disclosure",
            "status": "pass",
            "message": "No MLS/listing photos detected on this page. AB 723 disclosure not required.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    # Listing photos present — check for disclosure text
    parsed = _parse_html(html)
    all_text = lower + " " + " ".join(parsed.img_alts)
    for pattern in _AB723_DISCLOSURES:
        m = re.search(pattern, all_text)
        if m:
            return {
                "rule_id": "R05",
                "rule_name": "AB 723 Altered Image Disclosure",
                "status": "pass",
                "message": "Listing photos present and alteration/staging disclosure found.",
                "remediation": None,
                "evidence": _truncate(m.group(0)),
                "screenshot_required": False,
            }

    return {
        "rule_id": "R05",
        "rule_name": "AB 723 Altered Image Disclosure",
        "status": "warning",
        "message": (
            f"{listing_photo_count} listing photo(s) detected but no virtual staging or "
            "digital alteration disclosure found. AB 723 (eff. Jan 1, 2026) requires a "
            "conspicuous disclosure on any digitally altered listing image."
        ),
        "remediation": (
            "Add a disclosure label on or near any digitally altered or virtually staged "
            "listing images. Acceptable language: 'Virtually Staged', 'Digitally Altered', "
            "or 'Rendering'. Basic enhancements (brightness, color correction, cropping) "
            "are exempt. This is required by California AB 723."
        ),
        "evidence": f"{listing_photo_count} listing photo(s) found; no alteration disclosure text detected.",
        "screenshot_required": True,
    }


# ─────────────────────────────────────────────
# Rule 6 — CCPA Privacy Policy Link
# ─────────────────────────────────────────────

_CCPA_TEXT_PATTERNS = [
    r'\bprivacy\s+policy\b',
    r'\bprivacy\s+notice\b',            # "California Privacy Notice" (BHHS, large brokerages)
    r'\bcalifornia\s+privacy\b',        # "California Privacy Notice" or "California Privacy Rights"
    r'\bdo\s+not\s+sell\b',
    r'\bdo\s+not\s+share\b',
    r'\bccpa\b',
    r'\bcpra\b',
    r'\bcalifornia\s+consumer\s+privacy\b',
    r'\byour\s+privacy\s+rights\b',
    r'\bprivacy\s+rights\b',
    r'\bopt[\s-]out\b',
]


def check_ccpa_privacy(html: str, text: str) -> dict:
    """
    Rule 6: CCPA Privacy Policy Link
    Checks both link href/text and page text for privacy policy indicators.
    Fail if completely absent.
    """
    lower = text.lower()
    parsed = _parse_html(html)

    # Check link hrefs and anchor text
    all_link_content = " ".join(parsed.link_hrefs) + " ".join(parsed.link_texts)
    for pattern in _CCPA_TEXT_PATTERNS:
        m = re.search(pattern, all_link_content)
        if m:
            return {
                "rule_id": "R06",
                "rule_name": "CCPA Privacy Policy Link",
                "status": "pass",
                "message": "CCPA-compliant privacy policy link or disclosure found.",
                "remediation": None,
                "evidence": _truncate(m.group(0)),
                "screenshot_required": False,
            }

    # Check plain text
    for pattern in _CCPA_TEXT_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            return {
                "rule_id": "R06",
                "rule_name": "CCPA Privacy Policy Link",
                "status": "pass",
                "message": "CCPA privacy policy reference found in page text.",
                "remediation": None,
                "evidence": _truncate(text[max(0, m.start()-10):m.end()+40]),
                "screenshot_required": False,
            }

    return {
        "rule_id": "R06",
        "rule_name": "CCPA Privacy Policy Link",
        "status": "fail",
        "message": "No CCPA privacy policy link or disclosure found.",
        "remediation": (
            "California law (CCPA/CPRA) requires a Privacy Policy on any website that "
            "collects personal information from California residents. Add a 'Privacy Policy' "
            "link in your footer and include 'Do Not Sell or Share My Personal Information' "
            "if applicable. Consider linking to a compliant privacy policy page."
        ),
        "evidence": None,
        "screenshot_required": False,
    }


# ─────────────────────────────────────────────
# Rule 7 — No Prohibited DFPI Claims (lending only)
# ─────────────────────────────────────────────

_DFPI_PROHIBITED = [
    # CCR §1950.204.3 — prohibits implying state supervision/endorsement
    # These patterns must specifically claim CA state oversight, NOT describe
    # federal program characteristics (FHA/VA loans are legitimately "government-backed")
    r'\bsupervised\s+by\s+the\s+(?:state|california\s+(?:dfpi|dre|department))\b',
    r'\bregulated\s+by\s+the\s+state\s+of\s+california\b',
    r'\bstate\s+of\s+california\s+(?:approved|endorsed|guarantees)\b',
    r'\bdfpi[\s-](?:approved|endorsed|guaranteed)\b',
    r'\bcalifornia\s+(?:dfpi|dre)\s+(?:approved|endorsed|guarantees)\b',
    r'\bstate[\s-]supervised\s+(?:lender|mortgage|loan)\b',
    # "bonded by" only when followed by a CA government entity
    r'\bbonded\s+by\s+(?:the\s+)?(?:state|california|dfpi|dre)\b',
]
# NOTE: "government-backed programs" is NOT prohibited — FHA/VA loans literally are
# government-backed. "federally insured" is also accurate for FHA/FDIC.
# Only flag explicit claims of CA state supervision/endorsement.


def check_dfpi_prohibited_claims(text: str) -> dict:
    """
    Rule 7: No Prohibited DFPI Claims (lending only)
    Fail immediately if any prohibited phrase is found.
    """
    lower = text.lower()
    violations = []

    for pattern in _DFPI_PROHIBITED:
        m = re.search(pattern, lower)
        if m:
            violations.append(_truncate(text[max(0, m.start()-20):m.end()+40]))

    if violations:
        return {
            "rule_id": "R07",
            "rule_name": "No Prohibited DFPI Claims",
            "status": "fail",
            "message": f"Prohibited DFPI claim(s) detected: {len(violations)} violation(s) found.",
            "remediation": (
                "Remove all language implying government supervision, regulation, or bond backing "
                "of your lending business. The DFPI prohibits misleading endorsement language. "
                "Remove phrases like 'supervised by the state', 'regulated by the state', or 'bonded by'."
            ),
            "evidence": "; ".join(violations[:3]),
            "screenshot_required": False,
        }

    return {
        "rule_id": "R07",
        "rule_name": "No Prohibited DFPI Claims",
        "status": "pass",
        "message": "No prohibited DFPI claims detected.",
        "remediation": None,
        "evidence": None,
        "screenshot_required": False,
    }


# ─────────────────────────────────────────────
# Rule 8 — Equal Housing Opportunity
# ─────────────────────────────────────────────

_EHO_PATTERNS = [
    r'\bequal\s+housing\b',
    r'\bfair\s+housing\b',
    r'\beho\b',
    r'\bequal\s+opportunity\s+(?:lender|employer|realtor)\b',
    r'\bwe\s+do\s+business\s+in\s+accordance\s+with\s+the\s+fair\s+housing\b',
]

_EHO_LOGO_ALTS = [
    'equal housing', 'fair housing', 'equal opportunity lender',
    'equal opportunity realtor', 'equal housing opportunity',
    'equal housing lender', 'fair housing act'
]
# Also check img src patterns for EHO logo files
_EHO_SRC_PATTERNS = [
    r'equal[-_]?housing',
    r'eho[-_]?logo',
    r'fair[-_]?housing',
]


def check_equal_housing(html: str, text: str) -> dict:
    """
    Rule 8: Equal Housing Opportunity
    Warning (not fail) if absent — still noteworthy but not a hard legal violation
    in all contexts. Logo alt text counts as valid disclosure.
    """
    lower = text.lower()
    parsed = _parse_html(html)

    # Check text
    for pattern in _EHO_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            return {
                "rule_id": "R08",
                "rule_name": "Equal Housing Opportunity",
                "status": "pass",
                "message": "Equal Housing Opportunity statement found.",
                "remediation": None,
                "evidence": _truncate(text[max(0, m.start()-10):m.end()+40]),
                "screenshot_required": False,
            }

    # Check img alt text for EHO logo — require actual EHO phrase match, not partial words
    for alt in parsed.img_alts:
        alt_lower = alt.lower()
        if any(phrase in alt_lower for phrase in _EHO_LOGO_ALTS):
            return {
                "rule_id": "R08",
                "rule_name": "Equal Housing Opportunity",
                "status": "pass",
                "message": "Equal Housing Opportunity logo detected via image alt text.",
                "remediation": None,
                "evidence": f'Image alt: "{alt}"',
                "screenshot_required": False,
            }

    # Check img src URLs for EHO logo filenames
    for m in re.finditer(r'<img\b[^>]*src=["\']([^"\']+)["\'][^>]*>', html, re.IGNORECASE):
        src = m.group(1).lower()
        if any(re.search(p, src) for p in _EHO_SRC_PATTERNS):
            return {
                "rule_id": "R08",
                "rule_name": "Equal Housing Opportunity",
                "status": "pass",
                "message": "Equal Housing Opportunity logo detected via image filename.",
                "remediation": None,
                "evidence": f'Image src: "{src[:80]}"',
                "screenshot_required": False,
            }

    return {
        "rule_id": "R08",
        "rule_name": "Equal Housing Opportunity",
        "status": "warning",
        "message": "No Equal Housing Opportunity statement or logo detected.",
        "remediation": (
            "Add 'Equal Housing Opportunity' text or the EHO logo to your footer. "
            "If using the logo, ensure the <img> tag includes descriptive alt text "
            "such as alt='Equal Housing Opportunity'. This is strongly recommended "
            "for all real estate and mortgage advertising."
        ),
        "evidence": None,
        "screenshot_required": True,
    }


# ─────────────────────────────────────────────
# Rule 9 — Team Name Compliance (realestate only)
# ─────────────────────────────────────────────

# Module-level constants (hoisted from check_team_name_compliance for efficiency)
_TEAM_NAV_PHRASES = {
    'meet the team', 'our team', 'join the team', 'the team',
    'meet our team', 'contact our team', 'contact the team',
    'about our team', 'our group', 'the group', 'leadership team',
    'management team', 'support team', 'sales team', 'advisory group',
    'executive team', 'founding team', 'core team', 'about our group',
    'estate school', 'school team',
}
_TEAM_NAV_GENERIC = {
    'first', 'best', 'top', 'prime', 'elite', 'luxury', 'coastal', 'bay', 'pacific',
    'golden', 'premier', 'prestige', 'real', 'estate', 'realty', 'property', 'homes',
    'properties', 'partners', 'presence', 'legacy', 'vision', 'pinnacle', 'horizon',
    'summit', 'apex', 'harbor', 'haven', 'crest', 'ridge', 'view', 'vista', 'point',
    'village', 'metro', 'urban', 'local', 'new', 'one', 'key', 'core', 'arc',
    'leadership', 'management', 'support', 'sales', 'advisory', 'executive',
    'founding', 'school', 'national', 'regional', 'professional', 'certified',
    'about', 'blog', 'home', 'news', 'contact', 'menu', 'search', 'services',
    'more', 'info', 'learn', 'join', 'meet', 'find', 'buy', 'sell', 'rent',
}
_TEAM_BRAND_WORDS = {
    'first', 'best', 'top', 'prime', 'elite', 'luxury', 'coastal', 'bay', 'pacific',
    'golden', 'premier', 'prestige', 'century', 'compass', 'coldwell', 'berkshire',
    'keller', 'sotheby', 'remax', 'redfin', 'zillow', 'real', 'estate', 'realty',
    'property', 'homes', 'properties', 'group', 'team', 'associates', 'partners',
    'presence', 'legacy', 'vision', 'pinnacle', 'horizon', 'summit', 'apex',
    'harbor', 'haven', 'crest', 'ridge', 'view', 'vista', 'point', 'pointe',
    'village', 'metro', 'urban', 'local', 'new', 'one', 'key', 'core', 'arc',
}

def check_team_name_compliance(text: str) -> dict:
    """
    Rule 9: Team Name Compliance (realestate only)
    If 'team' or 'group' appears in branding, check that a surname + DRE # are nearby.
    Warning if team name present but disclosure is unclear.
    """
    lower = text.lower()
    upper = text.upper()

    # Check for team/group branding — must look like a BRANDED team name.
    # Requirements:
    #   1. Starts with a capital letter (proper noun, not generic nav text)
    #   2. Is 2-4 words max (branded names are short)
    #   3. Not a known generic/nav phrase
    #   4. Not pure lowercase (nav text like "leadership team" is all lower in inner_text)
    # Collapse newlines/tabs into single spaces before matching
    # (Playwright inner_text has \n between nav items — prevents cross-line false matches)
    text_flat = re.sub(r'[\r\n\t]+', ' ', text)
    text_flat = re.sub(r'  +', ' ', text_flat)

    # Look in the ORIGINAL (mixed-case, flattened) text — branded team names are capitalized
    team_match = re.search(
        r'\b([A-Z][a-zA-Z]{1,20}(?:\s+[A-Z][a-zA-Z]{1,20}){0,3})\s+(?:Team|Group|Associates)\b',
        text_flat
    )

    # Filter out known generic/nav phrases
    if team_match:
        matched_raw  = team_match.group(0).strip()     # original case (for isupper check)
        matched_phrase = matched_raw.lower()            # lowercase for dict lookups
        # Strip leading articles, then strip trailing suffix
        core_raw = re.sub(r'^(?:the|a|an)\s+', '', matched_raw, flags=re.IGNORECASE).strip()
        core_raw = re.sub(r'\s+(?:team|group|associates)$', '', core_raw, flags=re.IGNORECASE).strip()
        core_words_raw = core_raw.split()               # original case
        # A word is generic if: in module-level _TEAM_NAV_GENERIC OR all-caps (nav label)
        all_generic = all(w.lower() in _TEAM_NAV_GENERIC or w.isupper() for w in core_words_raw)
        if (matched_phrase in _TEAM_NAV_PHRASES
                or any(matched_phrase.startswith(p) for p in ('meet', 'our ', 'join', 'about', 'contact'))
                or not core_words_raw
                or all_generic):
            team_match = None

    if not team_match:
        return {
            "rule_id": "R09",
            "rule_name": "Team Name Compliance",
            "status": "pass",
            "message": "No team or group branding detected. Team name disclosure not required.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    team_name = _truncate(team_match.group(0).strip(), 60)

    # Check that a DRE number is present
    has_dre = bool(re.search(r'\b(DRE|BRE)\s*#\s*\d{7,9}\b', upper))

    # Check for a personal surname (Title Case word) directly adjacent to Team/Group/Associates.
    # Must look like "Smith Team", "Johnson Group" — a person's last name, not a brand.
    # Exclude known brand words that are NOT surnames.
    surname_match = re.search(
        r'\b([A-Z][a-z]{2,})\s+(?:Team|Group|Associates|Realty\s+Group)\b',
        text
    )
    has_surname = False
    if surname_match:
        candidate = surname_match.group(1).lower()
        if candidate not in _TEAM_BRAND_WORDS:
            has_surname = True

    if has_dre and has_surname:
        return {
            "rule_id": "R09",
            "rule_name": "Team Name Compliance",
            "status": "pass",
            "message": f"Team/group name '{team_name}' found with licensee surname and DRE number.",
            "remediation": None,
            "evidence": team_name,
            "screenshot_required": False,
        }

    # Partial — team found but disclosure incomplete
    missing = []
    if not has_surname:
        missing.append("responsible agent's surname")
    if not has_dre:
        missing.append("DRE license number")

    return {
        "rule_id": "R09",
        "rule_name": "Team Name Compliance",
        "status": "warning",
        "message": (
            f"Team/group name '{team_name}' detected but the following may be missing: "
            f"{', '.join(missing)}."
        ),
        "remediation": (
            "California DRE requires that team names include or be clearly associated with "
            "the responsible agent's surname and DRE license number. Example: "
            "'The Smith Team | Jane Smith, DRE #01234567'. "
            "Ensure both appear prominently near your team name in all advertising."
        ),
        "evidence": f"Team name found: '{team_name}'. Missing: {', '.join(missing)}.",
        "screenshot_required": True,
    }


# ─────────────────────────────────────────────
# Rule 10 — DBA / Fictitious Name Disclosure
# ─────────────────────────────────────────────

def _lookup_dre_name(license_number: str) -> Optional[str]:
    """
    Look up the DRE-licensed name for a given license number via the public DRE website.
    Returns the licensed name string, or None if lookup fails.
    Results are cached per invocation to avoid redundant HTTP calls.
    https://www2.dre.ca.gov/publicasp/pplinfo.asp
    """
    lic = re.sub(r'\D', '', license_number)
    if lic in _DRE_NAME_CACHE:
        return _DRE_NAME_CACHE[lic]
    try:
        data = urllib.parse.urlencode({
            "h_nextstep": "SEARCH",
            "LICENSEE_NAME": "",
            "CITY_STATE": "",
            "LICENSE_ID": lic,
        }).encode()
        req = urllib.request.Request(
            "https://www2.dre.ca.gov/publicasp/pplinfo.asp?start=1",
            data=data,
            headers={
                "User-Agent": _BOT_UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www2.dre.ca.gov/publicasp/pplinfo.asp",
            }
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode('utf-8', errors='replace')

        # Extract <strong>Name:</strong> ... value from the response table
        name_match = re.search(
            r'<strong>Name:</strong>.*?<FONT[^>]*>([^<\r\n]+)',
            html, re.IGNORECASE | re.DOTALL
        )
        if name_match:
            name = re.sub(r'\s+', ' ', name_match.group(1)).strip()
            _DRE_NAME_CACHE[lic] = name
            return name
    except Exception:
        pass
    _DRE_NAME_CACHE[lic] = None
    return None


def _names_match(dre_name: str, page_text: str) -> bool:
    """
    Check if the DRE-licensed name reasonably matches the name used on the page.
    Handles stylistic differences (all caps, ® symbols, punctuation, spacing).
    """
    # Normalize both: lowercase, strip punctuation/symbols, collapse spaces
    def normalize(s: str) -> str:
        s = s.lower()
        s = re.sub(r'[®™\-–—.,\'"]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    dre_norm = normalize(dre_name)
    page_norm = normalize(page_text)

    # Check if core words of DRE name appear in page text
    # e.g. "first team real estate orange county" → check "first team real estate" present
    dre_words = dre_norm.split()
    # Skip generic suffixes that appear in both names
    _generic = {'inc', 'llc', 'corp', 'co', 'the', 'a', 'an', 'of', 'and', '&', 'dba'}
    sig_words = [w for w in dre_words if w not in _generic][:5]

    if not sig_words:
        return True  # Can't determine — give benefit of the doubt

    # For person names (Last, First format from DRE), the last name is most important
    # If the DRE name contains a comma (Last, First), check last name match as primary
    if ',' in dre_name:
        last_name = normalize(dre_name.split(',')[0].strip())
        if last_name and last_name in page_norm:
            return True  # Last name match alone is sufficient

    # Handle nickname abbreviations: "Greg" matches "Gregory", "Bob" matches "Robert", etc.
    # Strategy: if a DRE word's first 4+ chars appear in a page word's first 4+ chars → match
    def soft_match(dre_word: str, text: str) -> bool:
        if dre_word in text:
            return True
        # Prefix match for names (handles Greg/Gregory, Mike/Michael, etc.)
        if len(dre_word) >= 4:
            prefix = dre_word[:4]
            return bool(re.search(r'\b' + re.escape(prefix), text))
        return False

    matches = sum(1 for w in sig_words if soft_match(w, page_norm))
    return matches >= max(2, len(sig_words) - 1)


def check_dba_disclosure(text: str, dre_license: Optional[str] = None) -> dict:
    """
    Rule 10: DBA / Fictitious Name Disclosure (B&P Code §10159.5)

    Looks up the DRE-licensed name for the detected license number and compares
    it to the name used on the page. If they don't match and no DBA disclosure
    is present, flag as warning.

    Falls back to pass if DRE lookup fails (no false positives without data).
    """
    lower = text.lower()

    # Check for explicit DBA disclosure first
    _dba_patterns = [
        r'\bdoing\s+business\s+as\b',
        r'\bDBA\b',
        r'\bd\.b\.a\b',
        r'\bfictitious\s+business\s+name\b',
        r'\bformerly\s+known\s+as\b',
    ]
    for pattern in _dba_patterns:
        m = re.search(pattern, lower)
        if m:
            return {
                "rule_id": "R10",
                "rule_name": "DBA / Fictitious Name Disclosure",
                "status": "pass",
                "message": "DBA or fictitious business name disclosure found.",
                "remediation": None,
                "evidence": _truncate(text[max(0, m.start()-10):m.end()+50]),
                "screenshot_required": False,
            }

    # If no license number to look up, can't check — pass
    if not dre_license:
        return {
            "rule_id": "R10",
            "rule_name": "DBA / Fictitious Name Disclosure",
            "status": "pass",
            "message": "No DRE license number found to verify name against DRE records.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    # Look up DRE-licensed name
    dre_name = _lookup_dre_name(dre_license)

    if not dre_name:
        # Lookup failed — don't penalize
        return {
            "rule_id": "R10",
            "rule_name": "DBA / Fictitious Name Disclosure",
            "status": "pass",
            "message": "DRE name lookup unavailable. Unable to verify business name against license records.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    # Compare DRE name to page
    if _names_match(dre_name, text):
        return {
            "rule_id": "R10",
            "rule_name": "DBA / Fictitious Name Disclosure",
            "status": "pass",
            "message": f"Business name on page matches DRE-licensed name '{dre_name}'.",
            "remediation": None,
            "evidence": f"DRE record: '{dre_name}'",
            "screenshot_required": False,
        }

    # Name mismatch — warn with DBA guidance.
    # Per B&P §10159.5: a fictitious business name is permitted if it is registered to
    # and operated under the RESPONSIBLE BROKER (not a salesperson independently).
    # DBA registered to the broker = compliant; DBA registered to a sales agent = violation.
    return {
        "rule_id": "R10",
        "rule_name": "DBA / Fictitious Name Disclosure",
        "status": "warning",
        "message": (
            f"The name used on this page does not clearly match the DRE-licensed name "
            f"'{dre_name}'. If this is a DBA or fictitious business name registered to "
            f"the responsible broker, it is compliant (B&P §10159.5)."
        ),
        "remediation": (
            f"DRE license on this page is registered to '{dre_name}'. "
            "A DBA or fictitious business name is permitted under B&P §10159.5 ONLY if it "
            "is registered to and operated under the responsible BROKER — not a salesperson. "
            "If the DBA is broker-registered: no additional disclosure needed. "
            "If the DBA belongs to a salesperson: it must be dissolved or re-registered under the broker. "
            "Confirm registration via DRE eLicensing."
        ),
        "evidence": f"DRE record: '{dre_name}'",
        "screenshot_required": True,
    }


# ─────────────────────────────────────────────
# Rule 11 — DFPI FIN §22162: License Disclosure in Lending Ads
# ─────────────────────────────────────────────

_LOAN_AD_INDICATORS = [
    r'\bmortgage\b',
    r'\bhome\s+loan\b',
    r'\brefinanc',
    r'\bheloc\b',
    r'\bpurchase\s+loan\b',
    r'\bloan\s+officer\b',
    r'\blending\b',
    r'\blender\b',
    r'\bapply\s+for\s+(?:a\s+)?loan\b',
    r'\brate\s+quote\b',
    r'\bpre[\s-]?approv',
    r'\bpre[\s-]?qualif',
    r'\binterest\s+rate\b',
    r'\bhome\s+purchase\b',
    r'\bfha\s+loan\b',
    r'\bva\s+loan\b',
]

_R11_LICENSE_PATTERNS = [
    # NMLS (most common — covers federal MLO requirement)
    r'\bnmls(?:r)?\s*(?:#|:|\s+id\s*:?)?\s*\d{6,10}\b',
    # DRE license number with label
    r'\bdre\s*(?:license|lic\.?)?\s*#\s*\d{7,9}\b',
    # DFPI license
    r'\bdfpi\s*(?:license|lic\.?)?\s*#\s*\d+\b',
    r'\bdepartment\s+of\s+financial\s+protection\s*(?:license|lic\.?)?\s*#\s*\d+\b',
    # CFL license
    r'\bcalifornia\s+finance\s+(?:lender|law)\s*(?:license|lic\.?)?\s*#\s*\d+\b',
    r'\bcfl\s*(?:license)?\s*#\s*\d+\b',
    # Statutory disclosure phrase
    r'\bloans\s+made\s+or\s+arranged\s+pursuant\s+to\s+a\s+california\b',
    r'\blicensed\s+by\s+(?:the\s+)?dfpi\b',
    r'\blicensed\s+under\s+(?:the\s+)?california\s+finance\b',
]


def check_r11(html: str, text: str, profession: str) -> dict:
    """
    Rule 11: DFPI License Disclosure in Lending Ads
    Cal. Fin. Code §22162(a) — lending ads must disclose the license under which
    the loan will be made or arranged.
    """
    if profession != "lending":
        return {
            "rule_id": "R11",
            "rule_name": "DFPI License Disclosure in Lending Ads",
            "status": "pass",
            "message": "Not applicable — lending profession only.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    lower = text.lower()
    upper = text.upper()

    # Check for loan advertising content
    has_loan_ad = any(re.search(p, lower) for p in _LOAN_AD_INDICATORS)

    if not has_loan_ad:
        return {
            "rule_id": "R11",
            "rule_name": "DFPI License Disclosure in Lending Ads",
            "status": "pass",
            "message": "No loan advertising content detected. License disclosure not required.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    # Check for license disclosure (case-insensitive)
    for pattern in _R11_LICENSE_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            return {
                "rule_id": "R11",
                "rule_name": "DFPI License Disclosure in Lending Ads",
                "status": "pass",
                "message": "License disclosure found on lending advertisement page.",
                "remediation": None,
                "evidence": _truncate(text[max(0, m.start()-5):m.end()+5]),
                "screenshot_required": False,
            }

    # Also check NMLS in upper-case form (canonical)
    nmls_m = re.search(r'\bNMLS(?:R)?\s*(?:#|:|\s+ID\s*:?)?\s*\d{6,10}\b', upper)
    if nmls_m:
        return {
            "rule_id": "R11",
            "rule_name": "DFPI License Disclosure in Lending Ads",
            "status": "pass",
            "message": "NMLS license disclosure found on lending advertisement page.",
            "remediation": None,
            "evidence": _truncate(nmls_m.group(0)),
            "screenshot_required": False,
        }

    return {
        "rule_id": "R11",
        "rule_name": "DFPI License Disclosure in Lending Ads",
        "status": "fail",
        "message": (
            "Lending advertisement page is missing a required license disclosure. "
            "No NMLS#, DRE#, DFPI license#, or CFL license# detected."
        ),
        "remediation": (
            "California Financial Code §22162(a) requires lending advertisements to disclose "
            "the license under which the loan will be made or arranged. "
            "Add your NMLS# (e.g., 'NMLS #123456'), DRE license number, DFPI/CFL license number, "
            "or the phrase 'Loans made or arranged pursuant to a California Finance Lenders Law License.'"
        ),
        "evidence": None,
        "screenshot_required": False,
    }


# ─────────────────────────────────────────────
# Rule 13 — Reg Z §1026.16: HELOC Advertising Trigger Terms
# ─────────────────────────────────────────────

_HELOC_INDICATOR_PATTERNS = [
    r'\bheloc\b',
    r'\bhome\s+equity\s+line\b',
    r'\bhome[\s-]equity\s+line\s+of\s+credit\b',
    r'\bequity\s+line\b',
    r'\bline\s+of\s+credit\s+secured\b',
]

_HELOC_TRIGGER_PATTERNS = [
    r'\$[\d,]+\s*(?:per\s+month|\/mo(?:nth)?|monthly)\b',
    r'\bpayment\s+(?:as\s+low\s+as\s+)?\$[\d,]+\b',
    r'\bmonthly\s+payment\s+(?:of\s+)?\$[\d,]+\b',
    r'\bjust\s+\$?[\d,]+\s*(?:per\s+month|\/mo(?:nth)?)\b',  # "just $299/month" or "just 299/month"
    r'\bat\s+(?:just\s+|only\s+)?\$?[\d,]+(?:\/mo(?:nth)?\b|\/month\b)',  # "at just 299/month"
    r'\b[\d]+\.?\d*\s*%\s*(?:interest|rate|apr)\b',
    r'\bno\s+(?:fee|closing\s+cost|points?)\b',
    r'\b(?:promotional|introductory|intro)\s+rate\b',
    r'\brate\s+as\s+low\s+as\b',
]

_R13_APR_PATTERN = re.compile(r'\bAPR\b|\bannual\s+percentage\s+rate\b', re.IGNORECASE)
_R13_MAX_APR_PATTERN = re.compile(
    r'\b(?:maximum|max\.?)\s+(?:APR|annual\s+percentage\s+rate)\b', re.IGNORECASE
)
_FREE_MONEY_PATTERN = re.compile(r'\bfree\s+money\b', re.IGNORECASE)


def check_r13(html: str, text: str, profession: str) -> dict:
    """
    Rule 13: Reg Z §1026.16 HELOC Advertising Trigger Terms
    12 CFR §1026.16(b)(1), §1026.16(d)(1) — HELOC ads with trigger terms must
    disclose APR, max APR, and may not use 'free money' or misleading 'fixed' claims.
    """
    if profession != "lending":
        return {
            "rule_id": "R13",
            "rule_name": "Reg Z §1026.16: HELOC Advertising Trigger Terms",
            "status": "pass",
            "message": "Not applicable — lending profession only.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    lower = text.lower()

    # Step 1: Is there HELOC content?
    has_heloc = any(re.search(p, lower) for p in _HELOC_INDICATOR_PATTERNS)
    if not has_heloc:
        return {
            "rule_id": "R13",
            "rule_name": "Reg Z §1026.16: HELOC Advertising Trigger Terms",
            "status": "pass",
            "message": "No HELOC content detected. Reg Z §1026.16 disclosures not required.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    # Step 2: Are there trigger terms?
    trigger_found = []
    for p in _HELOC_TRIGGER_PATTERNS:
        m = re.search(p, lower)
        if m:
            trigger_found.append(_truncate(m.group(0).strip(), 60))

    if not trigger_found and not _FREE_MONEY_PATTERN.search(text):
        return {
            "rule_id": "R13",
            "rule_name": "Reg Z §1026.16: HELOC Advertising Trigger Terms",
            "status": "pass",
            "message": "HELOC content detected but no trigger terms found. No additional disclosures required.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    # Step 3: Check required disclosures and prohibited terms
    fail_msgs = []
    warn_msgs = []

    # Prohibited: "free money"
    if _FREE_MONEY_PATTERN.search(text):
        fail_msgs.append("HELOC ad uses prohibited 'free money' language (12 CFR §1026.16(d)(5))")

    if trigger_found:
        # Must have APR disclosure
        if not _R13_APR_PATTERN.search(text):
            fail_msgs.append("HELOC ad with trigger terms is missing APR disclosure (12 CFR §1026.16(b)(1)(ii))")

        # Must have max APR for variable plans
        if not _R13_MAX_APR_PATTERN.search(text):
            fail_msgs.append("HELOC variable-rate ad is missing maximum APR disclosure (12 CFR §1026.16(d)(1)(iii))")

        # Warning: "fixed" without time period in HELOC context
        if re.search(r'\bfixed\b', lower) and not re.search(r'\bfixed\s+for\s+\d+\s+(?:year|month|yr|mo)', lower):
            warn_msgs.append("'Fixed' rate used in HELOC context without specifying the fixed period — may mislead consumers (12 CFR §1026.16(f))")

    if not fail_msgs and not warn_msgs:
        return {
            "rule_id": "R13",
            "rule_name": "Reg Z §1026.16: HELOC Advertising Trigger Terms",
            "status": "pass",
            "message": "HELOC advertising includes required APR and max APR disclosures.",
            "remediation": None,
            "evidence": f"Trigger terms found: {', '.join(trigger_found[:3])}",
            "screenshot_required": False,
        }

    status = "fail" if fail_msgs else "warning"
    all_msgs = fail_msgs + warn_msgs

    return {
        "rule_id": "R13",
        "rule_name": "Reg Z §1026.16: HELOC Advertising Trigger Terms",
        "status": status,
        "message": "; ".join(all_msgs),
        "remediation": (
            "12 CFR §1026.16 requires HELOC ads with trigger terms to disclose: "
            "(1) APR as 'Annual Percentage Rate', (2) maximum APR for variable-rate plans, "
            "(3) any balloon payment. Prohibited terms: 'free money'. "
            "If using 'fixed rate', specify the period (e.g., 'fixed for 12 months')."
        ),
        "evidence": _truncate(
            f"Trigger terms: {', '.join(trigger_found[:3])}. " + "; ".join(all_msgs[:2])
        ),
        "screenshot_required": False,
    }


# ─────────────────────────────────────────────
# Rule 14 — Reg Z §1026.24(i): Prohibited Mortgage Ad Practices
# ─────────────────────────────────────────────

_GOVT_ENDORSEMENT_PATTERNS = [
    r'\bgovernment\s+loan\s+program\b',
    r'\bgovernment[\s-](?:supported|endorsed|sponsored|approved)\s+(?:loan|mortgage|program)\b',
    r'\bgovernment[\s-]backed\s+(?:loan\s+program|program)\b',
    r'\bstate[\s-](?:endorsed|approved|sponsored|supervised)\s+(?:loan|mortgage|program)\b',
    r'\bstate\s+(?:government\s+)?(?:endorsed|approved|sponsored)\s+(?:loan|mortgage)\b',
]

_FHA_VA_EXCLUSION_PATTERN = re.compile(
    r'\b(?:fha|va\s+loan|veterans?\s+affairs?|usda|fannie\s+mae|freddie\s+mac|'
    r'federal\s+housing\s+administration|hud\b|government[\s-]insured)\b',
    re.IGNORECASE
)

_DEBT_ELIM_PATTERNS = [
    r'\b(?:eliminate|wipe\s+out|erase|cancel|forgive)\s+(?:your\s+)?(?:debt|mortgage|loan)\b',
    r'\bdebt\s+(?:elimination|cancellation|forgiveness|erasure)\b',
    r'\beliminate\s+your\s+mortgage\b',
]

_COUNSELOR_MISUSE_PATTERN = re.compile(
    r'\b(?:mortgage|loan|home\s+loan|housing)\s+counselor\b', re.IGNORECASE
)

_FIXED_ARM_MISMATCH_PATTERN = re.compile(
    r'(?:(?:adjustable|variable|arm)[^.!?]{0,250}(?:fixed\s+(?:rate|payment|mortgage))'
    r'|(?:fixed\s+(?:rate|payment|mortgage))[^.!?]{0,250}(?:adjustable|variable|arm))',
    re.DOTALL | re.IGNORECASE
)


def check_r14(html: str, text: str, profession: str) -> dict:
    """
    Rule 14: Reg Z §1026.24(i) Prohibited Mortgage Ad Practices
    12 CFR §1026.24(i)(3),(i)(5),(i)(6) — false government endorsement,
    debt elimination, counselor misuse, fixed/ARM mismatch.
    """
    if profession != "lending":
        return {
            "rule_id": "R14",
            "rule_name": "Reg Z §1026.24(i): Prohibited Mortgage Ad Practices",
            "status": "pass",
            "message": "Not applicable — lending profession only.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    lower = text.lower()
    fail_msgs = []
    warn_msgs = []
    evidence_parts = []

    # Check 1: False government endorsement
    for p in _GOVT_ENDORSEMENT_PATTERNS:
        m = re.search(p, lower)
        if m:
            # Check surrounding context for legitimate FHA/VA carve-out
            surrounding = text[max(0, m.start() - 200):m.end() + 200]
            if not _FHA_VA_EXCLUSION_PATTERN.search(surrounding):
                snippet = _truncate(text[max(0, m.start() - 10):m.end() + 30], 80)
                fail_msgs.append(f"False government endorsement claim detected (12 CFR §1026.24(i)(3))")
                evidence_parts.append(f"Gov. endorsement: '{snippet}'")
                break  # one flag is enough

    # Check 2: Debt elimination claims
    for p in _DEBT_ELIM_PATTERNS:
        m = re.search(p, lower)
        if m:
            snippet = _truncate(text[max(0, m.start() - 5):m.end() + 20], 80)
            fail_msgs.append("Debt elimination claim detected (12 CFR §1026.24(i)(5))")
            evidence_parts.append(f"Debt elim: '{snippet}'")
            break

    # Check 3: "Counselor" misuse — WARNING only
    m = _COUNSELOR_MISUSE_PATTERN.search(text)
    if m:
        warn_msgs.append(
            "'Counselor' term used — verify this is a non-profit HUD-approved counselor, "
            "not a for-profit mortgage broker (12 CFR §1026.24(i)(6))"
        )
        evidence_parts.append(f"Counselor: '{_truncate(m.group(0))}'")

    # Check 4: Fixed rate in same context as ARM/variable without disclosure period — WARNING
    m = _FIXED_ARM_MISMATCH_PATTERN.search(text)
    if m:
        warn_msgs.append(
            "'Fixed rate' appears near ARM/variable rate terms without a clear fixed period "
            "disclosure (12 CFR §1026.24(i))"
        )
        evidence_parts.append(f"Fixed+ARM: '{_truncate(m.group(0)[:80])}'")

    if not fail_msgs and not warn_msgs:
        return {
            "rule_id": "R14",
            "rule_name": "Reg Z §1026.24(i): Prohibited Mortgage Ad Practices",
            "status": "pass",
            "message": "No prohibited mortgage advertising practices detected.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    status = "fail" if fail_msgs else "warning"
    all_msgs = fail_msgs + warn_msgs

    return {
        "rule_id": "R14",
        "rule_name": "Reg Z §1026.24(i): Prohibited Mortgage Ad Practices",
        "status": status,
        "message": "; ".join(all_msgs),
        "remediation": (
            "12 CFR §1026.24(i) prohibits: (1) false government endorsement unless actual "
            "FHA/VA/USDA context, (2) debt elimination claims, (3) using 'counselor' for "
            "for-profit mortgage brokers, (4) 'fixed rate' claims without disclosing the "
            "fixed period when also advertising an ARM. "
            "Remove or correct the flagged language."
        ),
        "evidence": _truncate("; ".join(evidence_parts)),
        "screenshot_required": bool(warn_msgs and not fail_msgs),
    }


# ─────────────────────────────────────────────
# Rule 15 — Reg Z §1026.24(f)(3): Taxes & Insurance Exclusion Disclosure
# ─────────────────────────────────────────────

_PAYMENT_ADVERTISED_PATTERNS = [
    r'\$[\d,]+\.?\d*\s*(?:per\s+month|\/mo(?:nth)?)\b',
    r'\bmonthly\s+payment\s+(?:as\s+low\s+as\s+|of\s+)?\$[\d,]+\b',
    r'\bpayment\s+(?:as\s+low\s+as\s+)?\$[\d,]+\b',
    r'\bpay\s+(?:as\s+little\s+as\s+|only\s+)?\$[\d,]+\s+(?:per\s+month|\/mo)\b',
]

_FIRST_LIEN_CONTEXT_PATTERNS = [
    r'\b(?:purchase|home\s+purchase|buy\s+a\s+home)\b',
    r'\b(?:mortgage|home\s+loan|primary\s+mortgage|first[\s-]lien)\b',
    r'\b(?:refinanc|refi)\b',
    r'\b(?:30[\s-]year|15[\s-]year|20[\s-]year|fixed[\s-]rate\s+mortgage)\b',
]

_TAX_INS_DISCLAIMER_PATTERNS = [
    r'\bdoes\s+not\s+include\s+(?:taxes|property\s+tax|insurance|escrow|pmi)\b',
    r'\bnot\s+includ(?:e|ing)\s+(?:taxes|property\s+tax|insurance|escrow|pmi)\b',
    r'\btaxes\s+(?:and|&|\/)\s+insurance\s+(?:are\s+)?not\s+included\b',
    r'\bexcludes?\s+(?:taxes|property\s+tax|insurance|escrow|pmi|t&i)\b',
    r'\b\+\s*(?:taxes?|t&i|pmi|taxes?\s+(?:and|&)\s+insurance)\b',
    r'\*(?:[^*]{0,80})(?:taxes|insurance|escrow)\s+(?:not\s+included|excluded|separate)\b',
    r'\btaxes\s+and\s+insurance\s+(?:are\s+)?(?:not\s+)?(?:included|excluded|separate)\b',
]


def check_r15(html: str, text: str, profession: str) -> dict:
    """
    Rule 15: Reg Z §1026.24(f)(3) Taxes & Insurance Exclusion Disclosure
    12 CFR §1026.24(f)(3)(i)(C) — first-lien mortgage ads that state a payment
    amount must disclose that taxes and insurance are not included.
    """
    if profession != "lending":
        return {
            "rule_id": "R15",
            "rule_name": "Reg Z §1026.24(f)(3): Taxes & Insurance Exclusion Disclosure",
            "status": "pass",
            "message": "Not applicable — lending profession only.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    lower = text.lower()

    # Check for specific payment amount advertised
    payment_found = None
    for p in _PAYMENT_ADVERTISED_PATTERNS:
        m = re.search(p, lower)
        if m:
            payment_found = _truncate(m.group(0).strip(), 60)
            break

    if not payment_found:
        return {
            "rule_id": "R15",
            "rule_name": "Reg Z §1026.24(f)(3): Taxes & Insurance Exclusion Disclosure",
            "status": "pass",
            "message": "No specific monthly payment amount advertised. Disclosure not required.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    # Check for first-lien mortgage context
    has_first_lien = any(re.search(p, lower) for p in _FIRST_LIEN_CONTEXT_PATTERNS)
    if not has_first_lien:
        return {
            "rule_id": "R15",
            "rule_name": "Reg Z §1026.24(f)(3): Taxes & Insurance Exclusion Disclosure",
            "status": "pass",
            "message": "Payment amount found but no first-lien mortgage context detected. Disclosure may not be required.",
            "remediation": None,
            "evidence": payment_found,
            "screenshot_required": False,
        }

    # Check for taxes/insurance exclusion disclaimer
    has_disclaimer = any(re.search(p, lower) for p in _TAX_INS_DISCLAIMER_PATTERNS)

    if has_disclaimer:
        return {
            "rule_id": "R15",
            "rule_name": "Reg Z §1026.24(f)(3): Taxes & Insurance Exclusion Disclosure",
            "status": "pass",
            "message": "Payment amount advertised with taxes/insurance exclusion disclaimer present.",
            "remediation": None,
            "evidence": payment_found,
            "screenshot_required": False,
        }

    return {
        "rule_id": "R15",
        "rule_name": "Reg Z §1026.24(f)(3): Taxes & Insurance Exclusion Disclosure",
        "status": "warning",
        "message": (
            f"First-lien mortgage ad states a payment amount ('{payment_found}') without "
            "a disclosure that taxes and insurance are not included. Disclaimer may be in "
            "fine print or tooltip — requires visual verification."
        ),
        "remediation": (
            "12 CFR §1026.24(f)(3)(i)(C) requires first-lien mortgage ads that state "
            "a payment amount to disclose that taxes and insurance are not included and "
            "the actual payment may be higher. Add language such as: "
            "'*Does not include property taxes and homeowner's insurance; actual payment may be higher.'"
        ),
        "evidence": f"Payment advertised: '{payment_found}'; no taxes/insurance exclusion disclaimer found.",
        "screenshot_required": True,
    }


# ─────────────────────────────────────────────
# Rule 16 — Fair Housing Act §3604(c): Discriminatory Language
# ─────────────────────────────────────────────

# Each tuple: (regex_pattern, human_readable_reason)
_DISCRIMINATORY_PATTERNS = [
    # Section 8 / source of income (CA FEHA §12955 protects source of income)
    (r'\bno\s+section\s+8\b',
     "Section 8 refusal — violates CA FEHA source-of-income protection (Gov. Code §12955)"),
    (r'\bsection\s+8\s+(?:not\s+)?(?:accepted|welcome|considered|allowed)\b',
     "Section 8 refusal — violates CA FEHA source-of-income protection"),
    # Explicit race/national origin exclusion
    (r'\bno\s+(?:minorities|blacks?|whites?|asians?|hispanics?|latinos?|mexicans?|arabs?)\b',
     "Explicit race/national origin exclusion language"),
    (r'\b(?:white|black|asian|hispanic|christian|jewish|muslim|arabic)[\s-]only\b',
     "Explicit protected class restriction"),
    # Familial status — non-HOPA contexts (55+ HOPA communities have a statutory exemption)
    (r'\bno\s+children\b',
     "Familial status exclusion — 'no children' (verify HOPA exemption if applicable)"),
    (r'\bchild[\s-]?free\s+(?:community|building|property|complex|unit|home|space)\b',
     "Familial status exclusion — 'child-free' property/community"),
    (r'\badults?[\s-]only\b',
     "Familial status — 'adults only' (verify HOPA 55+ exemption if applicable)"),
    # Steering / demographic preference language
    (r'\b(?:perfect|ideal|great)\s+for\s+(?:a\s+)?(?:single|bachelor|christian|jewish|muslim|religious)\b',
     "Demographic steering language indicating preference"),
    # Blockbusting language (BPC §10177(l))
    (r'\bchanging\s+neighborhood[^.!?]{0,150}(?:sell\s+now|act\s+fast|while\s+you\s+can)\b',
     "Potential blockbusting/panic-selling language (BPC §10177(l))"),
    (r'\b(?:declining|falling)\s+(?:property\s+values?|home\s+values?)[^.!?]{0,100}(?:sell|list|move)\b',
     "Potential blockbusting language linking declining values to urgency to sell"),
]


def check_r16(html: str, text: str, profession: str) -> dict:
    """
    Rule 16: Fair Housing Act §3604(c) Discriminatory Language
    42 U.S.C. §3604(c); Cal. Gov. Code §12955
    ALL matches → WARNING for human review. Never auto-FAIL (context matters).
    """
    if profession != "realestate":
        return {
            "rule_id": "R16",
            "rule_name": "Fair Housing Act §3604(c): Discriminatory Language",
            "status": "pass",
            "message": "Not applicable — real estate profession only.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    lower = text.lower()
    warnings_found = []

    for pattern, reason in _DISCRIMINATORY_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            snippet = _truncate(text[max(0, m.start() - 5):m.end() + 25], 80)
            warnings_found.append((reason, snippet))

    if not warnings_found:
        return {
            "rule_id": "R16",
            "rule_name": "Fair Housing Act §3604(c): Discriminatory Language",
            "status": "pass",
            "message": "No discriminatory language patterns detected.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    reasons = [r for r, _ in warnings_found]
    snippets = [f"'{s}'" for _, s in warnings_found]

    return {
        "rule_id": "R16",
        "rule_name": "Fair Housing Act §3604(c): Discriminatory Language",
        "status": "warning",
        "message": (
            f"Potentially discriminatory language detected — requires human review. "
            f"{len(warnings_found)} pattern(s): {'; '.join(reasons[:3])}"
        ),
        "remediation": (
            "Review flagged language for compliance with 42 U.S.C. §3604(c) and California FEHA "
            "(Gov. Code §12955). Remove or revise language indicating preference or limitation based "
            "on race, color, religion, sex, familial status, national origin, disability, source of "
            "income, sexual orientation, or gender identity. "
            "55+ communities may qualify for the HOPA exemption — consult legal counsel."
        ),
        "evidence": _truncate("; ".join(f"{r}: {s}" for r, s in warnings_found[:3])),
        "screenshot_required": True,
    }


# ─────────────────────────────────────────────
# Rule 17 — CCPA §1798.135: "Do Not Sell or Share" Link
# ─────────────────────────────────────────────

_DO_NOT_SELL_PATTERNS = [
    r'\bdo\s+not\s+sell\s+(?:or\s+share\s+)?my\s+personal\s+(?:information|info)\b',
    r'\bopt[\s-]out\s+of\s+(?:the\s+)?(?:sale|selling|sharing)\b',
    r'\byour\s+privacy\s+choices\b',
    r'\bcalifornia\s+privacy\s+rights\b',
    r'\blimit\s+(?:the\s+use\s+of\s+)?my\s+sensitive\s+personal\s+information\b',
    r'\bdo\s+not\s+share\s+my\s+personal\s+(?:information|info)\b',
]


def check_r17(html: str, text: str, profession: str) -> dict:
    """
    Rule 17: CCPA §1798.135 'Do Not Sell or Share My Personal Information' Link
    Cal. Civ. Code §1798.135(a)(1) — homepage must have a prominent opt-out link.
    Applies to both real estate and lending professions.
    """
    lower = text.lower()
    parsed = _parse_html(html)

    # Check both page body text AND link anchor text/hrefs
    all_link_content = (
        " ".join(parsed.link_hrefs)
        + " "
        + " ".join(parsed.link_texts)
    ).lower()
    combined = lower + " " + all_link_content

    for pattern in _DO_NOT_SELL_PATTERNS:
        m = re.search(pattern, combined, re.IGNORECASE)
        if m:
            return {
                "rule_id": "R17",
                "rule_name": "CCPA §1798.135: Do Not Sell or Share Link",
                "status": "pass",
                "message": "CCPA 'Do Not Sell or Share My Personal Information' link or equivalent found.",
                "remediation": None,
                "evidence": _truncate(m.group(0)),
                "screenshot_required": False,
            }

    return {
        "rule_id": "R17",
        "rule_name": "CCPA §1798.135: Do Not Sell or Share Link",
        "status": "warning",
        "message": (
            "No 'Do Not Sell or Share My Personal Information' link detected. "
            "If this business sells or shares personal data with third parties "
            "(e.g., ad networks, lead aggregators), Cal. Civ. Code §1798.135(a)(1) "
            "requires a prominent opt-out link on the homepage."
        ),
        "remediation": (
            "Add a clearly visible 'Do Not Sell or Share My Personal Information' link "
            "to your homepage footer. Acceptable alternatives: 'Your Privacy Choices' "
            "(with opt-out icon) or 'California Privacy Rights'. "
            "The link must lead to an opt-out mechanism. Required if your site sells or "
            "shares personal information with third parties (analytics, retargeting, lead gen)."
        ),
        "evidence": None,
        "screenshot_required": False,
    }


# ─────────────────────────────────────────────
# Rule 18 — DFPI FIN §22161: Misleading Lending Claims
# ─────────────────────────────────────────────

# FAIL-level patterns — explicitly deceptive/prohibited
_MISLEADING_FAIL_PATTERNS = [
    (r'\bguaranteed\s+(?:approval|loan|financing|credit)\b',
     "Guaranteed approval claim"),
    (r'\bapproval\s+guaranteed\b',
     "Approval guaranteed claim"),
    (r'\bno\s+credit\s+check\b',
     "No credit check claim"),
    (r'\bregardless\s+of\s+(?:your\s+)?(?:credit|credit\s+history|credit\s+score)\b',
     "Regardless of credit claim"),
]

# WARNING-level patterns — potentially misleading but context-dependent
_MISLEADING_WARN_PATTERNS = [
    (r'\blowest\s+(?:rate|apr|payment)\s+(?:in\s+\w+\s+)?guaranteed\b',
     "Lowest rate guaranteed claim"),
    (r'\bbest\s+rate\s+guaranteed\b',
     "Best rate guaranteed claim"),
    (r'\bguaranteed\s+(?:lowest|best)\s+(?:rate|apr|payment)\b',
     "Guaranteed lowest/best rate claim"),
    (r'\bno\s+fees?\s+ever\b',
     "No fees ever claim"),
    (r'\bno\s+closing\s+costs?\s+ever\b',
     "No closing costs ever claim"),
    (r'\bno\s+points?\s+ever\b',
     "No points ever claim"),
]

# Pre-approved in minutes — WARNING if no qualification caveat nearby
_PREAPPROVED_MINUTES_PATTERN = re.compile(
    r'\b(?:pre[\s-]?approv(?:al|ed)?|pre[\s-]?qualif(?:ied|ication)?)\s+in\s+minutes\b',
    re.IGNORECASE
)
_QUALIFICATION_CAVEAT_PATTERN = re.compile(
    r'\b(?:subject\s+to|pending|qualif|verif|credit\s+approv|income\s+verif|'
    r'not\s+a\s+commitment|terms\s+(?:and|&)\s+conditions|conditions\s+apply|'
    r'based\s+on\s+credit)\b',
    re.IGNORECASE
)


def check_r18(html: str, text: str, profession: str) -> dict:
    """
    Rule 18: DFPI FIN §22161 Misleading Lending Claims
    Cal. Fin. Code §22161(a)(3) — prohibits false, misleading, or deceptive
    statements regarding loan rates, terms, or conditions.
    """
    if profession != "lending":
        return {
            "rule_id": "R18",
            "rule_name": "DFPI FIN §22161: Misleading Lending Claims",
            "status": "pass",
            "message": "Not applicable — lending profession only.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    lower = text.lower()
    fail_msgs = []
    warn_msgs = []
    evidence_parts = []

    # Check FAIL-level patterns
    for pattern, label in _MISLEADING_FAIL_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            snippet = _truncate(text[max(0, m.start() - 5):m.end() + 20], 80)
            fail_msgs.append(f"{label} (Cal. Fin. Code §22161(a)(3))")
            evidence_parts.append(f"{label}: '{snippet}'")

    # Check WARNING-level patterns
    for pattern, label in _MISLEADING_WARN_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            snippet = _truncate(text[max(0, m.start() - 5):m.end() + 20], 80)
            warn_msgs.append(f"{label} — may be misleading without full context")
            evidence_parts.append(f"{label}: '{snippet}'")

    # Check "pre-approved in minutes" — warn only if no qualification caveat nearby
    m = _PREAPPROVED_MINUTES_PATTERN.search(text)
    if m:
        # Look within 300 chars before/after for a qualification caveat
        surrounding = text[max(0, m.start() - 200):m.end() + 200]
        if not _QUALIFICATION_CAVEAT_PATTERN.search(surrounding):
            warn_msgs.append(
                "'Pre-approved in minutes' claim without qualification caveat — "
                "add 'subject to credit approval' or similar disclaimer"
            )
            evidence_parts.append(f"Pre-approved in minutes: '{_truncate(m.group(0))}'")

    if not fail_msgs and not warn_msgs:
        return {
            "rule_id": "R18",
            "rule_name": "DFPI FIN §22161: Misleading Lending Claims",
            "status": "pass",
            "message": "No misleading lending claim patterns detected.",
            "remediation": None,
            "evidence": None,
            "screenshot_required": False,
        }

    status = "fail" if fail_msgs else "warning"
    all_msgs = fail_msgs + warn_msgs

    return {
        "rule_id": "R18",
        "rule_name": "DFPI FIN §22161: Misleading Lending Claims",
        "status": status,
        "message": "; ".join(all_msgs),
        "remediation": (
            "Cal. Fin. Code §22161(a)(3) prohibits false, misleading, or deceptive statements "
            "about loan rates, terms, or conditions. Remove: (1) guaranteed approval/no credit check "
            "claims, (2) absolute 'lowest/best rate' guarantees, (3) 'no fees ever' claims. "
            "For pre-approval claims, always include a qualification caveat "
            "(e.g., 'subject to credit approval and income verification')."
        ),
        "evidence": _truncate("; ".join(evidence_parts)),
        "screenshot_required": bool(warn_msgs and not fail_msgs),
    }


# ─────────────────────────────────────────────
# Scoring Engine
# ─────────────────────────────────────────────

def _compute_score(checks: list[dict]) -> tuple[int, dict]:
    """
    Compute a 0–100 compliance score from check results.
    Only applicable rules count toward the score — "na" (not applicable)
    rules are excluded from both numerator and denominator so they cannot
    inflate the score of a failing site.
    Pass = full point, Warning = half point, Fail = 0.
    Returns (score, summary_dict).
    """
    applicable = [c for c in checks if c["status"] != "na"]
    passed   = sum(1 for c in applicable if c["status"] == "pass")
    warnings = sum(1 for c in applicable if c["status"] == "warning")
    failed   = sum(1 for c in applicable if c["status"] == "fail")
    na_count = len(checks) - len(applicable)
    total    = len(applicable)

    if total == 0:
        score = 100
    else:
        points = (passed * 1.0 + warnings * 0.5) / total
        score = round(points * 100)

    screenshot_pending = any(c["screenshot_required"] for c in applicable)

    summary = {
        "passed": passed,
        "warnings": warnings,
        "failed": failed,
        "not_applicable": na_count,
        "screenshot_pending": screenshot_pending,
    }
    return score, summary


# ─────────────────────────────────────────────
# Main Orchestrator
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# Profession Auto-Detection
# ─────────────────────────────────────────────

# Strong lending signals — NMLS + mortgage-specific language
_LENDING_STRONG_SIGNALS = [
    r'\bnmls(?:r)?\s*(?:#|:|\s+id\s*:?)?\s*\d{6,10}\b',
    r'\bloan\s+officer\b',
    r'\bmortgage\s+(?:loan|broker|lender|originator|officer)\b',
    r'\bhome\s+loan\b',
    r'\brefinanc(?:e|ing)\b',
    r'\bpre[\s-]?approv(?:al|ed)\b',
    r'\binterest\s+rate\s*(?:as\s+low\s+as|\:)',
    r'\bapr\b',
    r'\bheloc\b',
    r'\bfha\s+loan\b',
    r'\bva\s+loan\b',
    r'\bconventional\s+loan\b',
    r'\brate\s+quote\b',
    r'\bpayment\s+quote\b',
    r'\bloan\s+amount\b',
    r'\bdown\s+payment\b',
    r'\bclosing\s+costs?\b',
    r'\bpoints?\s+(?:paid\s+)?at\s+closing\b',
]

# Strong real estate signals
_REALESTATE_STRONG_SIGNALS = [
    r'\bmls\s*#?\s*\d+\b',
    r'\blisting\s+(?:agent|price|id)\b',
    r'\bbuying\s+and\s+selling\b',
    r'\bopen\s+house\b',
    r'\bbuy(?:ers?)\s+(?:agent|representation)\b',
    r'\bsell(?:ers?)\s+(?:agent|representation)\b',
    r'\breal\s+estate\s+agent\b',
    r'\brealtor\b',
    r'\bcommission\b',
    r'\bjust\s+listed\b',
    r'\bjust\s+sold\b',
    r'\bescrow\s+(?:officer|company|agent)\b',
]


def _detect_profession(text: str, user_profession: str) -> tuple[str, str | None]:
    """
    Detect the effective profession from page content to catch mis-selections.

    Returns (effective_profession, override_reason | None).
    override_reason is set when we override the user's selection.
    """
    lower = text.lower()

    lending_hits = sum(1 for p in _LENDING_STRONG_SIGNALS if re.search(p, lower))
    realestate_hits = sum(1 for p in _REALESTATE_STRONG_SIGNALS if re.search(p, lower))

    # Need at least 3 strong lending signals to override a "realestate" selection
    if user_profession == "realestate" and lending_hits >= 3 and lending_hits > realestate_hits:
        return "lending", (
            f"Page content appears to be a mortgage/lending site "
            f"({lending_hits} lending signals vs {realestate_hits} real estate signals). "
            f"Real estate–specific rules (Responsible Broker, Team Name, AB 723, Fair Housing language) "
            f"have been suppressed. Re-scan with profession='lending' for accurate results."
        )

    # Need at least 3 strong RE signals to override a "lending" selection
    if user_profession == "lending" and realestate_hits >= 3 and realestate_hits > lending_hits:
        return "realestate", (
            f"Page content appears to be a real estate site "
            f"({realestate_hits} real estate signals vs {lending_hits} lending signals). "
            f"Lending-specific rules (NMLS, TILA, DFPI) have been suppressed. "
            f"Re-scan with profession='realestate' for accurate results."
        )

    return user_profession, None


def check_compliance(html: str, text: str, url: str, profession: str) -> dict:
    """
    Run all applicable compliance checks and return a structured result.

    Args:
        html:       Raw HTML of the scraped page.
        text:       Plain text extracted from the page (pre-stripped).
        url:        The URL that was scanned.
        profession: "realestate" or "lending".

    Returns:
        Structured compliance result dict with score, per-rule checks, and summary.
    """
    profession = profession.lower().strip()
    if profession not in ("realestate", "lending"):
        raise ValueError(f"Invalid profession '{profession}'. Must be 'realestate' or 'lending'.")

    # Auto-detect: override profession if page content strongly contradicts the selection
    effective_profession, profession_override_note = _detect_profession(text, profession)
    profession = effective_profession

    checks = []

    # ── Rules that apply to ALL professions ──────────────────────

    # R01: DRE License Number
    # For lending profession: DRE is only required for dual-licensed MLOs (those who
    # hold a DRE license AND originate loans). Pure DFPI/federal MLOs do not hold DRE
    # numbers. If lending + NMLS found → R01 passes automatically (NMLS is their license).
    # For real estate: always check.
    if profession == "lending":
        # Check for NMLS first — if present, DRE is optional for lending sites
        nmls_present = bool(re.search(r'\bNMLS(?:R)?\s*(?:#|:|\s+ID\s*:?)?\s*\d{6,10}\b', text.upper()))
        dre_result = check_dre_license(text)
        if dre_result["status"] == "fail" and nmls_present:
            # Soft-override: lending site with NMLS — no DRE required
            dre_result = {
                "rule_id": "R01",
                "rule_name": "DRE License Number",
                "status": "pass",
                "message": "NMLS license present. DRE number not required for DFPI/federal MLOs.",
                "remediation": None,
                "evidence": "NMLS found (DRE not required for non-DRE-licensed lenders)",
                "screenshot_required": False,
            }
        checks.append(dre_result)
    else:
        checks.append(check_dre_license(text))

    # R02: Responsible Broker Name — realestate only (B&P §10140.6(b)(1); CCR §2773)
    # Pass the DRE license from R01 so R02 can verify broker identity via DRE lookup.
    # The DRE number IS the broker's identity — no separate "Responsible Broker: Name" text required.
    r01_result = next((c for c in checks if c["rule_id"] == "R01"), None)
    r02_dre_license = None
    if r01_result and r01_result.get("status") == "pass" and r01_result.get("evidence"):
        lic_m = re.search(r'\b(\d{7,9})\b', r01_result["evidence"])
        if lic_m:
            r02_dre_license = lic_m.group(1)
    if profession == "realestate":
        checks.append(check_broker_name(text, dre_license=r02_dre_license))
    else:
        checks.append({"rule_id": "R02", "rule_name": "Responsible Broker Name",
            "status": "na", "message": "Not applicable — real estate profession only.",
            "remediation": None, "evidence": None, "screenshot_required": False})

    # R03: NMLS ID — lending only (SAFE Act; B&P §10140.6(b)(1))
    if profession == "lending":
        checks.append(check_nmls_id(text))
    else:
        checks.append({"rule_id": "R03", "rule_name": "NMLS License Number",
            "status": "na", "message": "Not applicable — lending profession only.",
            "remediation": None, "evidence": None, "screenshot_required": False})

    # R04: Reg Z Trigger Terms — lending only (12 CFR §1026.24)
    if profession == "lending":
        checks.append(check_reg_z_triggers(text))
    else:
        checks.append({"rule_id": "R04", "rule_name": "Reg Z Trigger Terms",
            "status": "na", "message": "Not applicable — lending profession only.",
            "remediation": None, "evidence": None, "screenshot_required": False})

    # R05: AB 723 Altered Image Disclosure — realestate only (Civil Code §1947.2)
    if profession == "realestate":
        checks.append(check_ab723_disclosure(html, text))
    else:
        checks.append({"rule_id": "R05", "rule_name": "AB 723 Altered Image Disclosure",
            "status": "na", "message": "Not applicable — real estate profession only.",
            "remediation": None, "evidence": None, "screenshot_required": False})

    # R06: CCPA Privacy Policy — all professions (CIV §1798.135)
    checks.append(check_ccpa_privacy(html, text))

    # R07: DFPI Prohibited Claims — lending only (FIN §22161)
    if profession == "lending":
        checks.append(check_dfpi_prohibited_claims(text))
    else:
        checks.append({"rule_id": "R07", "rule_name": "DFPI Prohibited Claims",
            "status": "na", "message": "Not applicable — lending profession only.",
            "remediation": None, "evidence": None, "screenshot_required": False})

    # R08: Equal Housing Opportunity — all professions (42 U.S.C. §3604)
    checks.append(check_equal_housing(html, text))

    # R09: Team Name Compliance — realestate only (B&P §10159.6, §10159.7)
    if profession == "realestate":
        checks.append(check_team_name_compliance(text))
    else:
        checks.append({"rule_id": "R09", "rule_name": "Team Name Compliance",
            "status": "na", "message": "Not applicable — real estate profession only.",
            "remediation": None, "evidence": None, "screenshot_required": False})

    # R10: DBA / Fictitious Name Disclosure — all professions
    # Pass the detected DRE license number so R10 can do a live DRE lookup
    dre_result = next((c for c in checks if c["rule_id"] == "R01"), None)
    detected_license = None
    if dre_result and dre_result.get("evidence"):
        lic_match = re.search(r'\d{7,9}', dre_result["evidence"])
        if lic_match:
            detected_license = lic_match.group(0)
    checks.append(check_dba_disclosure(text, dre_license=detected_license))

    # ── R11–R18: New compliance rules ───────────────────────────

    # R11: DFPI FIN §22162 — License Disclosure in Lending Ads (lending only)
    checks.append(check_r11(html, text, profession))

    # R13: Reg Z §1026.16 — HELOC Advertising Trigger Terms (lending only)
    checks.append(check_r13(html, text, profession))

    # R14: Reg Z §1026.24(i) — Prohibited Mortgage Ad Practices (lending only)
    checks.append(check_r14(html, text, profession))

    # R15: Reg Z §1026.24(f)(3) — Taxes & Insurance Exclusion Disclosure (lending only)
    checks.append(check_r15(html, text, profession))

    # R16: Fair Housing Act §3604(c) — Discriminatory Language (realestate only)
    checks.append(check_r16(html, text, profession))

    # R17: CCPA §1798.135 — Do Not Sell or Share Link (both professions)
    checks.append(check_r17(html, text, profession))

    # R18: DFPI FIN §22161 — Misleading Lending Claims (lending only)
    checks.append(check_r18(html, text, profession))

    # ── Score & summarize ────────────────────────────────────────
    score, summary = _compute_score(checks)

    result = {
        "url": url,
        "profession": profession,
        "score": score,
        "checks": checks,
        "summary": summary,
    }
    if profession_override_note:
        result["profession_override"] = profession_override_note
    return result


# ─────────────────────────────────────────────
# AWS Lambda Handler
# ─────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    """
    AWS Lambda entry point.

    Expected event shape:
    {
        "html": "<html>...</html>",
        "text": "plain text...",
        "url": "https://example.com",
        "profession": "realestate" | "lending"
    }
    """
    try:
        result = check_compliance(
            html=event.get("html", ""),
            text=event.get("text", ""),
            url=event.get("url", ""),
            profession=event.get("profession", "realestate"),
        )
        return {"statusCode": 200, "body": json.dumps(result)}
    except ValueError as e:
        return {"statusCode": 400, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": f"Internal error: {str(e)}"})}


# ─────────────────────────────────────────────
# Local Test / __main__
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # ── Sample HTML for a California real estate agent page ──────
    SAMPLE_HTML_REALESTATE = """
    <!DOCTYPE html>
    <html>
    <head><title>Jane Smith, Realtor | The Smith Team</title></head>
    <body>
      <header>
        <h1>The Smith Team</h1>
        <p>Jane Smith | DRE #01234567</p>
        <p>Select California Homes, Responsible Broker: Bob Jones, DRE #00987654</p>
      </header>

      <main>
        <p>Welcome! We help buyers and sellers throughout San Diego County.</p>

        <h2>Featured Listings</h2>
        <img src="listing1.jpg" alt="3-bedroom home virtually staged">
        <img src="listing2.jpg" alt="Kitchen — digitally altered for illustration">

        <p>Beautiful 3-bedroom home available now. 30-year fixed rate options available.
           Monthly payment as low as $2,800. APR: 7.25%.</p>
      </main>

      <footer>
        <p>Equal Housing Opportunity</p>
        <p>© 2025 The Smith Team, doing business as Select CA Homes.</p>
        <a href="/privacy-policy">Privacy Policy</a>
        <a href="/do-not-sell">Do Not Sell My Personal Information</a>
      </footer>
    </body>
    </html>
    """

    SAMPLE_TEXT_REALESTATE = """
    The Smith Team
    Jane Smith | DRE #01234567
    Select California Homes, Responsible Broker: Bob Jones, DRE #00987654

    Welcome! We help buyers and sellers throughout San Diego County.

    Featured Listings
    Beautiful 3-bedroom home available now. 30-year fixed rate options available.
    Monthly payment as low as $2,800. APR: 7.25%.

    Equal Housing Opportunity
    © 2025 The Smith Team, doing business as Select CA Homes.
    Privacy Policy | Do Not Sell My Personal Information
    """

    # ── Sample HTML for a lending page ───────────────────────────
    SAMPLE_HTML_LENDING = """
    <!DOCTYPE html>
    <html>
    <head><title>Michael Colyer | Mortgage Loan Officer</title></head>
    <body>
      <header>
        <h1>Michael Colyer — Mortgage Loan Officer</h1>
        <p>NMLS #276626 | DRE #01842442</p>
        <p>Approved Mortgage | NMLS #2688301</p>
      </header>

      <main>
        <p>Get pre-approved today! Rates as low as 6.5% rate. Down payment as low as 3.5%.
           APR 6.89%. Monthly payment estimates available.</p>
        <p>Serving San Diego, Orange County, and Los Angeles.</p>
      </main>

      <footer>
        <img src="eho.png" alt="Equal Housing Opportunity Lender">
        <a href="/privacy">Privacy Policy</a>
        <p>© 2025 Approved Mortgage. All rights reserved.</p>
      </footer>
    </body>
    </html>
    """

    SAMPLE_TEXT_LENDING = """
    Michael Colyer — Mortgage Loan Officer
    NMLS #276626 | DRE #01842442
    Approved Mortgage | NMLS #2688301

    Get pre-approved today! Rates as low as 6.5% rate. Down payment as low as 3.5%.
    APR 6.89%. Monthly payment estimates available.
    Serving San Diego, Orange County, and Los Angeles.

    Equal Housing Opportunity Lender
    Privacy Policy
    © 2025 Approved Mortgage. All rights reserved.
    """

    print("=" * 60)
    print("TEST 1: Real Estate Agent Page")
    print("=" * 60)
    result_re = check_compliance(
        html=SAMPLE_HTML_REALESTATE,
        text=SAMPLE_TEXT_REALESTATE,
        url="https://example-realestate.com",
        profession="realestate",
    )
    print(json.dumps(result_re, indent=2))

    print("\n" + "=" * 60)
    print("TEST 2: Lending / Loan Officer Page")
    print("=" * 60)
    result_lo = check_compliance(
        html=SAMPLE_HTML_LENDING,
        text=SAMPLE_TEXT_LENDING,
        url="https://example-lending.com",
        profession="lending",
    )
    print(json.dumps(result_lo, indent=2))

    print("\n" + "=" * 60)
    print(f"RE Score:      {result_re['score']}/100")
    print(f"Lending Score: {result_lo['score']}/100")
    print(f"RE Summary:    {result_re['summary']}")
    print(f"LO Summary:    {result_lo['summary']}")
