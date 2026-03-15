import json
import boto3
from playwright.sync_api import sync_playwright
import re
from urllib.parse import urlparse

VALID_PROFESSIONS = {'lending', 'realestate'}

HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'POST,OPTIONS',
    'Content-Type': 'application/json'
}

def lambda_handler(event, context):
    """
    AWS Lambda handler for website compliance scanning
    """
    try:
        # Parse request
        body = json.loads(event.get('body', '{}'))
        url = body.get('url')
        profession = body.get('profession')

        if not url or not profession:
            return {
                'statusCode': 400,
                'headers': HEADERS,
                'body': json.dumps({'error': 'URL and profession are required'})
            }

        # Stage 1 fix: reject unknown profession values instead of silently defaulting
        if profession not in VALID_PROFESSIONS:
            return {
                'statusCode': 400,
                'headers': HEADERS,
                'body': json.dumps({
                    'error': f"Invalid profession '{profession}'. Must be one of: {', '.join(VALID_PROFESSIONS)}"
                })
            }

        # Validate URL
        if not url.startswith('http'):
            url = f'https://{url}'

        # Scrape website
        content = scrape_website(url)

        # Run compliance checks
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

def scrape_website(url):
    """Scrape website content using Playwright"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until='networkidle', timeout=30000)

        # Get text content
        content = page.evaluate('() => document.body.innerText')

        browser.close()
        return content.lower()

def run_compliance_checks(content, profession):
    """Run compliance rule checks"""

    # Define compliance rules
    rules = {
        'lending': {
            'federal': [
                {
                    'id': 'tila_apr',
                    'name': 'TILA/Reg Z - APR Disclosure (Indicator)',
                    # Stage 2 will add DOM-proximity checking; for now this is a surface-level signal only
                    'description': (
                        'Surface-level indicator: "APR" and a percentage appear on the page. '
                        'Note: Reg Z requires APR to appear in close proximity to any triggering term '
                        '(rate, payment, term) and be at least as conspicuous as the interest rate. '
                        'This check cannot verify proximity or prominence — consult a compliance attorney.'
                    ),
                    'check': lambda c: bool(re.search(r'\bapr\b', c)) and bool(re.search(r'\d+\.?\d*%', c)),
                    'source_url': 'https://www.consumerfinance.gov/rules-policy/regulations/1026/'
                },
                {
                    'id': 'ecoa_equal_housing',
                    'name': 'ECOA/Reg B - Equal Housing Lender',
                    'description': 'Equal Housing Lender statement must be present',
                    'check': lambda c: bool(re.search(r'equal housing|equal opportunity|fair housing', c)),
                    'source_url': 'https://www.consumerfinance.gov/rules-policy/regulations/1002/'
                },
                {
                    'id': 'safe_nmls',
                    'name': 'SAFE Act - NMLS Number',
                    # Stage 1 fix: warn when only one NMLS found; SAFE Act requires individual MLO + company IDs
                    'description': (
                        'SAFE Act requires each individual MLO\'s NMLS ID to appear in close proximity '
                        'to their name, in addition to the company NMLS ID. A single NMLS number may '
                        'indicate only the company ID is present. Status "warn" means only one NMLS '
                        'number was detected.'
                    ),
                    'check': lambda c: bool(re.search(r'nmls\s*#?\s*\d+', c)),
                    'check_warn': lambda c: len(re.findall(r'nmls\s*#?\s*\d+', c)) < 2,
                    'source_url': 'https://www.nmlsconsumeraccess.org/'
                },
                {
                    'id': 'can_spam_address',
                    'name': 'CAN-SPAM - Physical Address (Best Practice)',
                    # Stage 1 fix: add PO Box branch; clarify this applies to emails, not websites per se
                    'description': (
                        'CAN-SPAM requires a physical mailing address (including P.O. Boxes) in '
                        'commercial emails. For websites, this is a best practice rather than a strict '
                        'legal requirement. Accepts street addresses and P.O. Box formats.'
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
                    # Stage 1 fix: require exactly 8 digits per Title 10 CCR §2773
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
                    # Stage 1 fix: require "privacy policy" phrase + check for "do not sell" / opt-out
                    'description': (
                        'CPRA requires a published privacy policy, a Notice at Collection, disclosures '
                        'of consumer rights, and a "Do Not Sell or Share My Personal Information" link '
                        'if applicable. This check looks for a privacy policy reference and opt-out language.'
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
                    'description': 'Equal Housing Opportunity statement must be present',
                    'check': lambda c: bool(re.search(r'equal housing|fair housing|equal opportunity', c)),
                    'source_url': 'https://www.hud.gov/program_offices/fair_housing_equal_opp/fair_housing_act_overview'
                },
                {
                    'id': 'can_spam_address',
                    'name': 'CAN-SPAM - Physical Address (Best Practice)',
                    'description': (
                        'CAN-SPAM requires a physical mailing address (including P.O. Boxes) in '
                        'commercial emails. For websites, this is a best practice rather than a strict '
                        'legal requirement. Accepts street addresses and P.O. Box formats.'
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
                    # Stage 1 fix: require "responsible broker" or "broker of record", not just "broker"
                    'description': (
                        'CA DRE RE27 requires the responsible broker\'s licensed name and DRE license '
                        'number on all first-point-of-contact materials. This check looks for '
                        '"responsible broker" or "broker of record" language.'
                    ),
                    'check': lambda c: bool(re.search(r'responsible\s+broker|broker\s+of\s+record', c)),
                    'source_url': 'https://www.dre.ca.gov/files/pdf/re27.pdf'
                }
            ]
        }
    }

    # Run checks
    profession_rules = rules[profession]
    checks = []

    for category, category_rules in profession_rules.items():
        for rule in category_rules:
            passed = rule['check'](content)

            # Determine status: compliant / warn / missing
            if passed:
                warn_fn = rule.get('check_warn')
                if warn_fn and warn_fn(content):
                    status = 'warn'
                else:
                    status = 'compliant'
            else:
                status = 'missing'

            # Capture matched evidence substring instead of generic "Found on page"
            evidence = None
            if passed:
                match = re.search(
                    rule['check'].__code__.co_consts[1]  # fallback
                    if False else r'nmls\s*#?\s*\d+|dre\s*#?\s*\d{8}|apr|equal housing|fair housing|privacy policy|responsible broker|broker of record|p\.?o\.?\s*box|\d+\s+[^,]+,\s*[a-z]+',
                    content
                )
                evidence = match.group(0).strip() if match else 'Found on page'

            checks.append({
                'id': rule['id'],
                'name': rule['name'],
                'description': rule['description'],
                'category': category,
                'status': status,
                'source_url': rule['source_url'],
                'evidence': evidence if status != 'missing' else None,
                'recommendation': None if status == 'compliant' else (
                    f"Review {rule['name']} — indicator found but may need verification"
                    if status == 'warn' else
                    f"Add {rule['name'].lower()} to your website"
                )
            })

    # Calculate summary (warn counts as partial — not compliant, not missing)
    compliant = len([c for c in checks if c['status'] == 'compliant'])
    warnings = len([c for c in checks if c['status'] == 'warn'])
    missing = len([c for c in checks if c['status'] == 'missing'])

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

# For local testing
if __name__ == '__main__':
    test_event = {
        'body': json.dumps({
            'url': 'https://ratewatch.thecolyerteam.com',
            'profession': 'lending'
        })
    }
    result = lambda_handler(test_event, None)
    print(json.dumps(result, indent=2))
