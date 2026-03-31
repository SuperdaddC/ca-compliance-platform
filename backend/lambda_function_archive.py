import json
import re
from playwright.sync_api import sync_playwright

VALID_PROFESSIONS = {'lending', 'realestate'}

HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'POST,OPTIONS',
    'Content-Type': 'application/json'
}

# ---------------------------------------------------------------------------
# TILA proximity constants (Stage 2)
# A "triggering term" under Reg Z is any mention of a specific rate, payment,
# or loan term that obligates APR disclosure nearby.
# ---------------------------------------------------------------------------
TILA_TRIGGERING_TERMS = re.compile(
    r'(\d+\.?\d*\s*%\s*(interest|rate|fixed|variable|arm)|'
    r'\$[\d,]+\.?\d*\s*(per\s+month|\/mo|monthly\s+payment)|'
    r'\d+\s*-?\s*year\s+(fixed|arm|loan|mortgage)|'
    r'(fixed|variable)\s+rate)',
    re.IGNORECASE
)
TILA_APR_PATTERN = re.compile(r'\bapr\b', re.IGNORECASE)
TILA_PROXIMITY_WINDOW = 400  # characters each side of a triggering term


def check_tila_proximity(content: str) -> tuple[bool, str | None]:
    """
    Stage 2 DOM-aware TILA check.
    Finds every triggering term and checks whether 'APR' appears within
    TILA_PROXIMITY_WINDOW characters on either side.
    Returns (passed: bool, evidence: str | None).
    """
    for match in TILA_TRIGGERING_TERMS.finditer(content):
        start = max(0, match.start() - TILA_PROXIMITY_WINDOW)
        end = min(len(content), match.end() + TILA_PROXIMITY_WINDOW)
        window = content[start:end]
        apr_match = TILA_APR_PATTERN.search(window)
        if apr_match:
            # Return a readable snippet showing the triggering term + nearby APR
            snippet_start = max(0, match.start() - 80)
            snippet_end = min(len(content), match.end() + 80)
            snippet = content[snippet_start:snippet_end].replace('\n', ' ').strip()
            return True, f'…{snippet}…'
    # APR exists on the page but not near any triggering term — warn
    if TILA_APR_PATTERN.search(content):
        return None, 'APR found on page but not detected near a rate/payment triggering term'
    return False, None


def lambda_handler(event, context):
    """AWS Lambda handler for website compliance scanning"""
    try:
        body = json.loads(event.get('body', '{}'))
        url = body.get('url')
        profession = body.get('profession')

        if not url or not profession:
            return {
                'statusCode': 400,
                'headers': HEADERS,
                'body': json.dumps({'error': 'URL and profession are required'})
            }

        if profession not in VALID_PROFESSIONS:
            return {
                'statusCode': 400,
                'headers': HEADERS,
                'body': json.dumps({
                    'error': (
                        f"Invalid profession '{profession}'. "
                        f"Must be one of: {', '.join(sorted(VALID_PROFESSIONS))}"
                    )
                })
            }

        if not url.startswith('http'):
            url = f'https://{url}'

        content = scrape_website(url)
        results = run_compliance_checks(content, profession)

        return {
            'statusCode': 200,
            'headers': HEADERS,
            'body': json.dumps(results)
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'headers': HEADERS,
            'body': json.dumps({'error': str(e)})
        }


def scrape_website(url: str) -> str:
    """
    Stage 2: scrape both innerText and stripped innerHTML so compliance
    disclosures in alt attributes, meta tags, aria-labels, or hidden
    footers (display:none) are captured. browser.close() is always called
    via try/finally to prevent Lambda process leaks.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until='networkidle', timeout=30000)

            # Visible text (fast, human-readable)
            inner_text = page.evaluate('() => document.body.innerText') or ''

            # Full HTML stripped to plain text — catches alt text, aria-labels,
            # hidden compliance footers, and meta content attributes
            raw_html = page.evaluate('() => document.body.innerHTML') or ''
            # Strip all HTML tags; collapse whitespace
            stripped_html = re.sub(r'<[^>]+>', ' ', raw_html)
            stripped_html = re.sub(r'\s+', ' ', stripped_html)

            # Also pull <head> meta tags (description, keywords, og:title etc.)
            head_html = page.evaluate(
                '() => document.head ? document.head.innerHTML : ""'
            ) or ''
            head_text = re.sub(r'<[^>]+>', ' ', head_html)

            combined = f'{inner_text}\n{stripped_html}\n{head_text}'
            return combined.lower()
        finally:
            # Stage 2 fix: always close browser regardless of error
            browser.close()


def run_compliance_checks(content: str, profession: str) -> dict:
    """Run all compliance rule checks for the given profession."""

    # ------------------------------------------------------------------
    # Rule definitions
    # ------------------------------------------------------------------
    rules = {
        'lending': {
            'federal': [
                {
                    'id': 'tila_apr',
                    'name': 'TILA/Reg Z - APR Proximity Disclosure',
                    'description': (
                        'Stage 2 DOM-proximity check: verifies that "APR" appears within ~400 characters '
                        'of a Reg Z triggering term (specific rate, monthly payment, or loan term). '
                        'A "warn" status means APR was found on the page but not near a triggering term. '
                        'Note: this check cannot verify visual prominence or variable-rate disclosures — '
                        'consult a compliance attorney for full Reg Z compliance.'
                    ),
                    'check_fn': 'tila_proximity',   # handled separately below
                    'source_url': 'https://www.consumerfinance.gov/rules-policy/regulations/1026/'
                },
                {
                    'id': 'ecoa_equal_housing',
                    'name': 'ECOA/Reg B - Equal Housing Lender',
                    'description': 'Equal Housing Lender statement must be present.',
                    'check': lambda c: bool(re.search(r'equal housing|equal opportunity|fair housing', c)),
                    'source_url': 'https://www.consumerfinance.gov/rules-policy/regulations/1002/'
                },
                {
                    'id': 'safe_nmls',
                    'name': 'SAFE Act - NMLS Number',
                    'description': (
                        'SAFE Act requires each individual MLO\'s NMLS ID in close proximity to their name, '
                        'plus the company NMLS ID. A single NMLS number may indicate only the company ID '
                        'is present. "warn" = only one NMLS number detected.'
                    ),
                    'check': lambda c: bool(re.search(r'nmls\s*#?\s*\d+', c)),
                    'check_warn': lambda c: len(re.findall(r'nmls\s*#?\s*\d+', c)) < 2,
                    'source_url': 'https://www.nmlsconsumeraccess.org/'
                },
                {
                    'id': 'can_spam_address',
                    'name': 'CAN-SPAM - Physical Address (Best Practice)',
                    'description': (
                        'CAN-SPAM requires a physical mailing address (including P.O. Boxes) in commercial '
                        'emails. For websites this is a best practice, not a strict legal requirement. '
                        'Accepts street addresses and P.O. Box formats.'
                    ),
                    'check': lambda c: (
                        bool(re.search(r'\d+\s+[^,]+,\s*[a-z]+,\s*[a-z]{2}\s*\d{5}', c)) or
                        bool(re.search(r'p\.?o\.?\s*box\s+\d+', c))
                    ),
                    'source_url': 'https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business'
                }
            ],
            'state': [
                {
                    'id': 'ca_dre_license',
                    'name': 'CA DRE - License Disclosure',
                    'description': (
                        'CA DRE (Title 10 CCR §2773) requires an 8-digit DRE license number. '
                        'Individual and company DRE numbers must be displayed.'
                    ),
                    'check': lambda c: bool(re.search(r'dre\s*#?\s*\d{8}', c)),
                    'source_url': 'https://www.dre.ca.gov/files/pdf/forms/re559.pdf'
                },
                {
                    'id': 'ca_ccpa_privacy',
                    'name': 'CA CCPA/CPRA - Privacy Notice',
                    'description': (
                        'CPRA requires a published privacy policy, a Notice at Collection, consumer rights '
                        'disclosures, and a "Do Not Sell or Share My Personal Information" link if applicable. '
                        '"warn" = privacy policy found but no opt-out language detected.'
                    ),
                    'check': lambda c: bool(re.search(r'privacy policy', c)),
                    'check_warn': lambda c: not bool(re.search(r'do not sell|opt.?out', c)),
                    'source_url': 'https://oag.ca.gov/privacy/ccpa'
                }
            ]
        },
        'realestate': {
            'federal': [
                {
                    'id': 'fair_housing_logo',
                    'name': 'Fair Housing Act - Equal Housing',
                    'description': 'Equal Housing Opportunity statement must be present.',
                    'check': lambda c: bool(re.search(r'equal housing|fair housing|equal opportunity', c)),
                    'source_url': 'https://www.hud.gov/program_offices/fair_housing_equal_opp/fair_housing_act_overview'
                },
                {
                    'id': 'can_spam_address',
                    'name': 'CAN-SPAM - Physical Address (Best Practice)',
                    'description': (
                        'CAN-SPAM requires a physical mailing address (including P.O. Boxes) in commercial '
                        'emails. For websites this is a best practice, not a strict legal requirement.'
                    ),
                    'check': lambda c: (
                        bool(re.search(r'\d+\s+[^,]+,\s*[a-z]+,\s*[a-z]{2}\s*\d{5}', c)) or
                        bool(re.search(r'p\.?o\.?\s*box\s+\d+', c))
                    ),
                    'source_url': 'https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business'
                }
            ],
            'state': [
                {
                    'id': 'ca_dre_license',
                    'name': 'CA DRE - License Disclosure',
                    'description': (
                        'CA DRE (Title 10 CCR §2773) requires an 8-digit DRE license number. '
                        'Individual and company DRE numbers must be displayed.'
                    ),
                    'check': lambda c: bool(re.search(r'dre\s*#?\s*\d{8}', c)),
                    'source_url': 'https://www.dre.ca.gov/files/pdf/forms/re559.pdf'
                },
                {
                    'id': 'ca_broker_identification',
                    'name': 'CA DRE RE 27 - Responsible Broker',
                    'description': (
                        'CA DRE RE27 requires the responsible broker\'s licensed name and DRE license '
                        'number on all first-point-of-contact materials. Checks for "responsible broker" '
                        'or "broker of record" language.'
                    ),
                    'check': lambda c: bool(re.search(r'responsible\s+broker|broker\s+of\s+record', c)),
                    'source_url': 'https://www.dre.ca.gov/files/pdf/re27.pdf'
                }
            ]
        }
    }

    # Evidence pattern for generic rules (used when no specific match context is available)
    EVIDENCE_PATTERN = re.compile(
        r'nmls\s*#?\s*\d+|dre\s*#?\s*\d{8}|equal housing|fair housing|privacy policy'
        r'|responsible broker|broker of record|do not sell|opt.?out'
        r'|p\.?o\.?\s*box\s+\d+|\d+\s+[^,]{3,30},\s*[a-z]+,\s*[a-z]{2}\s*\d{5}'
    )

    profession_rules = rules[profession]
    checks = []

    for category, category_rules in profession_rules.items():
        for rule in category_rules:
            rule_id = rule['id']

            # ----------------------------------------------------------
            # TILA: use dedicated proximity checker
            # ----------------------------------------------------------
            if rule.get('check_fn') == 'tila_proximity':
                result, evidence = check_tila_proximity(content)
                if result is True:
                    status = 'compliant'
                elif result is None:
                    status = 'warn'
                else:
                    status = 'missing'
                    evidence = None
                recommendation = (
                    None if status == 'compliant' else
                    'APR found but not near a triggering term — ensure APR appears immediately next to any rate, payment, or term mention'
                    if status == 'warn' else
                    'Add APR disclosure adjacent to any rate or payment figures (Reg Z triggering terms)'
                )
                checks.append({
                    'id': rule_id,
                    'name': rule['name'],
                    'description': rule['description'],
                    'category': category,
                    'status': status,
                    'source_url': rule['source_url'],
                    'evidence': evidence,
                    'recommendation': recommendation
                })
                continue

            # ----------------------------------------------------------
            # Standard regex rules
            # ----------------------------------------------------------
            passed = rule['check'](content)

            if passed:
                warn_fn = rule.get('check_warn')
                status = 'warn' if (warn_fn and warn_fn(content)) else 'compliant'
            else:
                status = 'missing'

            # Capture actual matched text for evidence
            evidence = None
            if status != 'missing':
                m = EVIDENCE_PATTERN.search(content)
                evidence = m.group(0).strip() if m else 'Found on page'

            checks.append({
                'id': rule_id,
                'name': rule['name'],
                'description': rule['description'],
                'category': category,
                'status': status,
                'source_url': rule['source_url'],
                'evidence': evidence,
                'recommendation': (
                    None if status == 'compliant' else
                    f'Indicator found — verify {rule["name"]} meets full regulatory requirements'
                    if status == 'warn' else
                    f'Add {rule["name"].lower()} to your website'
                )
            })

    # Summary
    compliant = sum(1 for c in checks if c['status'] == 'compliant')
    warnings  = sum(1 for c in checks if c['status'] == 'warn')
    missing   = sum(1 for c in checks if c['status'] == 'missing')

    return {
        'checks': checks,
        'summary': {
            'total': len(checks),
            'compliant': compliant,
            'warnings': warnings,
            'missing': missing,
            'score': round(((compliant + warnings * 0.5) / len(checks)) * 100) if checks else 0
        },
        'disclaimer': (
            'This tool checks for common compliance indicators only. A passing or warning score is '
            'not a legal determination of compliance. Regulations have proximity, prominence, and '
            'content requirements that automated text scanning cannot fully verify. '
            'Consult a qualified compliance attorney before relying on these results.'
        )
    }


# ---------------------------------------------------------------------------
# Local testing
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    test_event = {
        'body': json.dumps({
            'url': 'https://ratewatch.thecolyerteam.com',
            'profession': 'lending'
        })
    }
    result = lambda_handler(test_event, None)
    print(json.dumps(result, indent=2))
