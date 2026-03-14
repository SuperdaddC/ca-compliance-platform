# California Real Estate & Lending Compliance Platform

## Overview
Automated compliance checking for California real estate and mortgage lending websites.

## Architecture
- **Frontend**: Netlify (React/Vue)
- **Backend**: AWS Lambda + Playwright (scraping)
- **Database**: Supabase (Postgres)
- **AI**: OpenClaw/Judy (compliance analysis)
- **Payments**: Stripe Billing

## Directory Structure
```
/frontend          - Netlify-hosted web app
/backend           - AWS Lambda functions
/docs              - Documentation and research
```

## Quick Start
1. Frontend: `cd frontend && npm install && npm run dev`
2. Backend: `cd backend && pip install -r requirements.txt`
3. Deploy: `git push origin main` (auto-deploys to Netlify)

## Compliance Rules (v1)
1. DRE License # present
2. Responsible broker name displayed
3. NMLS ID present (if MLO)
4. Reg Z trigger terms + APR disclosure
5. AB 723 altered image disclosure
6. CCPA privacy policy link
7. No prohibited DFPI claims
8. Equal Housing Opportunity

## Timeline
- Day 1-2: Infrastructure setup
- Day 3-7: Judy agent configuration
- Day 8-14: Rule engine + frontend
- Day 15-21: Stripe integration
- Day 22-30: Beta launch
- Day 31-60: Go-to-market

## Revenue Targets
- Day 60: $589 MRR (10 Pro + 1 Broker)
- Month 6: $6,885 MRR
- Year 1: $165,000 ARR

---
Built by The Colyer Team | NMLS #276626 | DRE #01842442
