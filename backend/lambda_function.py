import json
import boto3
from playwright.sync_api import sync_playwright
import re
from urllib.parse import urlparse

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
                'headers': {
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type',
                    'Access-Control-Allow-Methods': 'POST,OPTIONS'
                },
                'body': json.dumps({'error': 'URL and profession are required'})
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
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Allow-Methods': 'POST,OPTIONS',
                'Content-Type': 'application/json'
            },
            'body': json.dumps(results)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Allow-Methods': 'POST,OPTIONS'
            },
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
                    'name': 'TILA/Reg Z - APR Disclosure',
                    'description': 'APR must be displayed alongside interest rates using the term "APR"',
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
                    'description': 'Individual and company NMLS numbers must be displayed',
                    'check': lambda c: bool(re.search(r'nmls\s*#?\s*\d+', c)),
                    'source_url': 'https://www.nmlsconsumeraccess.org/'
                },
                {
                    'id': 'can_spam_address',
                    'name': 'CAN-SPAM - Physical Address',
                    'description': 'Business physical address must be displayed',
                    'check': lambda c: bool(re.search(r'\d+\s+[^,]+,\s*[a-z]+,\s*[a-z]{2}\s*\d{5}', c)),
                    'source_url': 'https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business'
                }
            ],
            'state': [
                {
                    'id': 'ca_dre_license',
                    'name': 'CA DRE - License Disclosure',
                    'description': 'Individual and company DRE numbers must be displayed',
                    'check': lambda c: bool(re.search(r'dre\s*#?\s*\d+', c)),
                    'source_url': 'https://www.dre.ca.gov/files/pdf/forms/re559.pdf'
                },
                {
                    'id': 'ca_ccpa_privacy',
                    'name': 'CA CCPA/CPRA - Privacy Notice',
                    'description': 'Privacy policy should be linked or referenced',
                    'check': lambda c: bool(re.search(r'privacy|privacy policy', c)),
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
                    'name': 'CAN-SPAM - Physical Address',
                    'description': 'Business physical address must be displayed',
                    'check': lambda c: bool(re.search(r'\d+\s+[^,]+,\s*[a-z]+,\s*[a-z]{2}\s*\d{5}', c)),
                    'source_url': 'https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business'
                }
            ],
            'state': [
                {
                    'id': 'ca_dre_license',
                    'name': 'CA DRE - License Disclosure',
                    'description': 'Individual and company DRE numbers must be displayed',
                    'check': lambda c: bool(re.search(r'dre\s*#?\s*\d+', c)),
                    'source_url': 'https://www.dre.ca.gov/files/pdf/forms/re559.pdf'
                },
                {
                    'id': 'ca_broker_identification',
                    'name': 'CA DRE RE 27 - Responsible Broker',
                    'description': 'Responsible broker must be identified',
                    'check': lambda c: bool(re.search(r'broker|responsible broker', c)),
                    'source_url': 'https://www.dre.ca.gov/files/pdf/re27.pdf'
                }
            ]
        }
    }
    
    # Run checks
    profession_rules = rules.get(profession, rules['lending'])
    checks = []
    
    for category, category_rules in profession_rules.items():
        for rule in category_rules:
            passed = rule['check'](content)
            checks.append({
                'id': rule['id'],
                'name': rule['name'],
                'description': rule['description'],
                'category': category,
                'status': 'compliant' if passed else 'missing',
                'source_url': rule['source_url'],
                'evidence': 'Found on page' if passed else None,
                'recommendation': None if passed else f"Add {rule['name'].lower()} to your website"
            })
    
    # Calculate summary
    compliant = len([c for c in checks if c['status'] == 'compliant'])
    missing = len([c for c in checks if c['status'] == 'missing'])
    
    return {
        'checks': checks,
        'summary': {
            'total': len(checks),
            'compliant': compliant,
            'missing': missing,
            'score': round((compliant / len(checks)) * 100) if checks else 0
        }
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
