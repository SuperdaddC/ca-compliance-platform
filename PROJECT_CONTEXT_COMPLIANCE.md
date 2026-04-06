# PROJECT CONTEXT — ComplyWithJudy Scanner Validation

**Read this file first. It tells you everything you need to pick up where the last session left off.**

**Last updated:** 2026-04-06
**Owner:** Mike Colyer (mike@thecolyerteam.com)

---

## 1. What This Project Is

ComplyWithJudy is a SaaS that scans California real estate agent and mortgage lender websites for regulatory compliance violations. The scanner is a deterministic Python rule engine (no AI/LLM in the scanner itself) that checks for DRE license numbers, NMLS IDs, Equal Housing logos, privacy policies, etc.

We are in the **active learning / validation phase** — scanning ~644 websites, then AI cross-referencing every result to find and fix bugs in the Python scanner before going to beta.

**The AI (Claude Code CLI) acts as the auditor, not part of the product.** The scanner must work standalone without any AI.

---

## 2. Two Machines

### Mac Mini (scanner server)
- **User:** `thecolyerteam@Michaels-Mac-mini`
- **Home:** `/Users/thecolyerteam`
- **Scanner server:** `~/complywithjudy-backend/scanner.py` — FastAPI + Playwright, runs via uvicorn on port 8000
- **Full repo:** `~/complywithjudy-repo/` — git clone of `SuperdaddC/ca-compliance-platform`
- **Venv:** `~/complywithjudy-backend/venv/`
- **Env vars:** `~/complywithjudy-backend/.env` — contains SUPABASE_URL, SUPABASE_SERVICE_KEY, ALLOWED_ORIGINS, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, API_KEYS
- **Role:** Runs the scanner API 24/7. Does NOT run batch scans.

### Windows Machine (Claude Code runs here)
- **User:** `ColyerTeam`
- **Project root:** `C:\Users\ColyerTeam\Developer\ca-compliance-platform\`
- **Python:** `py` command (Python 3.12)
- **Node.js:** Available
- **Role:** Source of truth for code edits. Runs batch scan script. Claude Code AI auditing happens here.

### Architecture
```
Windows (Claude Code)                   Mac Mini                          Supabase
─────────────────────                   ────────                          ────────
1. Edit scanner.py                      scanner.py (FastAPI+Playwright)
2. git push to GitHub ──────────────→   git pull + copy + restart uvicorn
3. scan_to_supabase.py ─── POST ────→   /api/scan endpoint
                                          scrapes site, runs checks
                          ←── JSON ────   returns results
4. Save to scan_results/*.json
5. AI audit (Claude agents fetch sites via WebFetch, compare to scanner results)
6. Upload audit records ──────────────────────────────────────────────→ audit_log table
```

---

## 3. Current State (as of 2026-04-06)

### Scanner Version: v1.2
- **17 bug fixes deployed** to Mac Mini
- **3 architectural improvements:** entity classification, privacy subpage scanning, improved EHO DOM detection
- Current git HEAD: `dc68877` (Add template placeholder phone detection)

### Scanning Progress
- **654 scan result JSONs** in `data/dre/scan_results/`
- **644 original targets** in `data/dre/enriched/scan_targets_all.csv`
- All targets have been scanned at least once

### AI Audit Progress
- **127 unique sites audited** across 25 batches
- **1,000 audit records** in Supabase `audit_log`
- **v1.2 accuracy: 88.0%** | Overall accuracy: 85.3%
- **~457 unaudited sites remain**
- Next batch: **batch 26**

### Deployment Status
- All code changes are deployed to Mac Mini
- Scanner is running and responding at `https://scanner.complywithjudy.com/api/scan`
- API key: `X-API-Key: judy_prod_xK9mW2pL7vR4nQ8s`

---

## 4. How to Deploy Scanner Changes to Mac Mini

After editing `backend/scanner.py` on Windows:
```bash
# On Windows:
cd C:/Users/ColyerTeam/Developer/ca-compliance-platform
git add backend/scanner.py && git commit -m "description" && git push origin main

# Mike runs on Mac Mini:
cd ~/complywithjudy-repo && git pull origin main && cp backend/scanner.py ~/complywithjudy-backend/scanner.py && cd ~/complywithjudy-backend && pkill -f uvicorn && sleep 2 && source venv/bin/activate && nohup uvicorn scanner:app --host 0.0.0.0 --port 8000 &
```
Press Enter after the last command. "appending output to nohup.out" is normal.

If scanner returns 503 "API keys not configured" → `API_KEYS` is missing from `~/complywithjudy-backend/.env`. Fix: `echo 'API_KEYS=judy_prod_xK9mW2pL7vR4nQ8s' >> ~/complywithjudy-backend/.env` then restart.

---

## 5. The Validation Loop (What to Do)

### Process per batch:
1. **Query Supabase** for already-audited sites (don't re-audit)
2. **Pick 5 unaudited sites** — random mix of RE + lending
3. **Fresh scan all 5** through the live `/api/scan` endpoint (gets results with current scanner code)
4. **Launch 5 parallel AI Agent subagents** — each fetches the site via WebFetch and independently evaluates every compliance check
5. **Compare scanner results vs AI results** — classify each as match/false_negative/false_positive/corrected/inconclusive_js
6. **Upload audit records** to Supabase `audit_log` via temp Node.js script
7. **If new bugs found** — fix scanner.py, commit, push, ask Mike to deploy, then continue
8. **Repeat**

### Key commands:

**Pick unaudited sites:**
```bash
cd C:/Users/ColyerTeam/Developer/ca-compliance-platform/data/dre/scan_results && node -e "
const fs = require('fs');
const https = require('https');
const url = 'https://mvdqlttptgwndoccotgg.supabase.co/rest/v1/audit_log?select=website';
const key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im12ZHFsdHRwdGd3bmRvY2NvdGdnIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NDk4MzY0NywiZXhwIjoyMDkwNTU5NjQ3fQ.bMDxU-eHXeCp-_nBnLKq4f7UCXIg9RGBFbCta3h6nmA';
https.get(url, {headers: {apikey: key, Authorization: 'Bearer ' + key}}, res => {
  let d = ''; res.on('data', c => d += c);
  res.on('end', () => {
    const audited = new Set(JSON.parse(d).map(r => r.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '').toLowerCase()));
    const files = fs.readdirSync('.').filter(f => f.endsWith('.json') && !f.includes('progress') && !f.startsWith('fresh_'));
    const candidates = [];
    for (const f of files) {
      const data = JSON.parse(fs.readFileSync(f));
      if (data.error || !data.checks) continue;
      if ((data.url||'').includes('forsale.godaddy') || (data.url||'').includes('sedo.com') || (data.url||'').includes('facebook.com')) continue;
      const urlNorm = (data.url || '').replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '').toLowerCase();
      if (audited.has(urlNorm)) continue;
      candidates.push({url: (data._target||{}).website || data.url, prof: data.profession});
    }
    const shuffled = candidates.sort(() => Math.random() - 0.5);
    const re = shuffled.filter(r => r.prof === 'realestate').slice(0,2);
    const lend = shuffled.filter(r => r.prof === 'lending').slice(0,3);
    const picks = [...re, ...lend].slice(0,5);
    for (const p of picks) console.log(JSON.stringify({url: p.url, prof: p.prof}));
    console.log('Remaining: ' + candidates.length);
  });
}).end();
"
```

**Fresh scan a site:**
```bash
curl -s -X POST "https://scanner.complywithjudy.com/api/scan" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: judy_prod_xK9mW2pL7vR4nQ8s" \
  -d '{"url":"https://example.com/","profession":"realestate"}'
```

**Upload audit records:** Write a temp Node.js file that POSTs an array to `https://mvdqlttptgwndoccotgg.supabase.co/rest/v1/audit_log`. Each object needs: website, profession, check_id, scanner_result, ai_result, verdict, rule_version, batch_number. Headers: apikey, Authorization (Bearer), Content-Type (application/json), Prefer (resolution=merge-duplicates). **Do NOT include extra fields** — API rejects unknown columns. Delete the temp file after.

**Get accuracy stats:**
```bash
node -e "
const https = require('https');
const url = 'https://mvdqlttptgwndoccotgg.supabase.co/rest/v1/audit_log?select=check_id,verdict,rule_version';
const key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im12ZHFsdHRwdGd3bmRvY2NvdGdnIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NDk4MzY0NywiZXhwIjoyMDkwNTU5NjQ3fQ.bMDxU-eHXeCp-_nBnLKq4f7UCXIg9RGBFbCta3h6nmA';
https.get(url, {headers: {apikey: key, Authorization: 'Bearer ' + key}}, res => {
  let d = ''; res.on('data', c => d += c);
  res.on('end', () => {
    const rows = JSON.parse(d);
    const v12 = rows.filter(r => r.rule_version === 'v1.2');
    let t=0, m=0, i=0;
    for (const r of v12) { t++; if(r.verdict==='match') m++; if(r.verdict==='inconclusive_js') i++; }
    console.log('v1.2: ' + t + ' records | Accuracy: ' + ((m/(t-i))*100).toFixed(1) + '%');
    let ta=0, ma=0, ia=0;
    for (const r of rows) { ta++; if(r.verdict==='match') ma++; if(r.verdict==='inconclusive_js') ia++; }
    console.log('Overall: ' + ta + ' records | Accuracy: ' + ((ma/(ta-ia))*100).toFixed(1) + '%');
  });
}).end();
"
```

---

## 6. Key Files

| File | Location | Purpose |
|------|----------|---------|
| `backend/scanner.py` | Windows repo (source of truth) | The scanner — FastAPI server, Playwright scraper, compliance rule engine |
| `backend/rule_engine.py` | Windows repo | Advanced rule engine (R01-R18) with DRE lookup. Referenced by scanner.py but scanner.py has its own checks too |
| `data/dre/scan_to_supabase.py` | Windows only | Batch scanner script — reads CSV, calls API, saves JSON + upserts to Supabase |
| `data/dre/enriched/scan_targets_all.csv` | Windows only | 664 scan targets from Apify Google Maps scrape |
| `data/dre/scan_results/*.json` | Windows only | Local JSON backup of every scan result |
| `supabase/schema.sql` | Repo | Database schema |
| `supabase/migrations/004_prospect_and_audit_tables.sql` | Repo | audit_log + prospect_scans tables, accuracy views |
| `frontend/src/pages/Landing.tsx` | Repo | ComplyWithJudy.com frontend (Netlify) |
| `COMPLYWITHJUDY_STATUS.md` | `C:\Users\ColyerTeam\Developer\` | Older status doc (this file supersedes it) |

---

## 7. Supabase

- **URL:** `https://mvdqlttptgwndoccotgg.supabase.co`
- **Service key:** `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im12ZHFsdHRwdGd3bmRvY2NvdGdnIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NDk4MzY0NywiZXhwIjoyMDkwNTU5NjQ3fQ.bMDxU-eHXeCp-_nBnLKq4f7UCXIg9RGBFbCta3h6nmA`

**Tables:**
- `prospect_scans` — Every scanned site with score, fails, warnings, contact info, outreach_status
- `audit_log` — AI vs scanner cross-reference (website, profession, check_id, scanner_result, ai_result, verdict, rule_version, batch_number)
- `scans` — Individual scan records from the frontend
- `user_subscriptions` — Stripe billing
- `scan_fingerprints` — Free scan abuse prevention

**Views:** `accuracy_by_check`, `accuracy_rolling`, `scan_summary`, `prospect_pipeline`

---

## 8. GitHub Repos

All under `github.com/SuperdaddC/`:

| Repo | Purpose | Deploys To |
|------|---------|-----------|
| `ca-compliance-platform` | Main project — scanner, frontend, supabase | Mac Mini (scanner), Netlify (frontend) |
| `quotes-thecolyerteam` | Mortgage quote viewer tool | Netlify (quotes.thecolyerteam.com) |
| `fsbo-site` | FSBO lead gen site | Not currently deployed (DNS issue) |
| `ratewatch-site` | Rate watch landing page | Netlify (ratewatch.thecolyerteam.com) |
| `thecolyerteam-hub` | Main hub site | Netlify (thecolyerteam.com) |
| `loan-application` | Online loan application | Netlify (apply.thecolyerteam.com) |
| `rwpartners-thecolyerteam` | Partner portal | Netlify |

---

## 9. All Scanner Fixes (17 total, all deployed)

### v1.0 → v1.1 (batches 1-5, 13 fixes)
1. EHL vs EHO severity — WARN when Opportunity present, FAIL only when absent
2. Placeholder email filter (`_has_real_email()`)
3. JS footer double-scroll for lazy-loading frameworks
4. Parked domain detection (GoDaddy, Sedo, etc.)
5. CCPA external privacy link filter (`_has_own_privacy_link()`)
6. DRE public lookup for broker identification (`lookup_dre_info()`)
7. EHO DOM detection tightened — word-bound "eho", visibility check, SVG nearby-text
8. EHL SVG/filename detection expanded
9. Platform-specific JS wait (KW, Compass, eXp, C21, etc.)
10. Physical address regex expanded (more street suffixes)
11. Subpage contact_info diagnostic note
12. Both scan endpoints (`/scan` and `/api/scan`) now share all fixes
13. DRE regex: BRE prefix, "CA DRE No.", "DRE No.", "License ID:" formats

### v1.1 → v1.2 (batches 21-25, 4 fixes)
14. Auto-entity classification (nonprofit, commercial_developer, property_manager, commercial_lender)
15. Privacy policy subpage scanning — follows privacy link, checks for CCPA content
16. EHO font-icon CSS class detection (ssi-eho, icon-eho, etc.)
17. Phantom EHO fix — require images to have rendered dimensions (getBoundingClientRect)
18. Template placeholder phone detection ((123)456-7890, 555-555-5555)

---

## 10. Known Remaining Issues (Not Code-Fixable)

These are architectural limitations, not regex bugs:

1. **EHO/EHL images with UUID filenames and no alt text** — Scanner improved DOM detection but can't visually analyze images. ~15% of remaining errors.
2. **Scanner only checks homepage** — TILA triggers, EHL logos, broker info on subpages are missed. Privacy subpage scanning was added but only follows the privacy link.
3. **Entity misclassification from Apify data** — Some targets are out-of-state, commercial, or non-RE businesses. Entity classifier catches most but not all.
4. **"Broker Associate" vs "Broker" in DRE lookup** — DRE classifies both as BROKER license type. Scanner can't distinguish without checking the "Broker Associate for:" field.
5. **CCPA "Do Not Sell" detection** — Some privacy pages embed the opt-out in a form/link that requires JS interaction, not just text.

---

## 11. Mike's Own Sites — All Compliant

All client-facing sites were audited and fixed for full CA lending/RE compliance on 2026-04-04:

| Site | Repo | Score |
|------|------|-------|
| complywithjudy.com | ca-compliance-platform/frontend | SaaS (privacy/TOS only) |
| quotes.thecolyerteam.com/quote-view.html | quotes-thecolyerteam | 12/12 |
| quotes.thecolyerteam.com/client.html | quotes-thecolyerteam | 12/12 |
| ratewatch.thecolyerteam.com | ratewatch-site | 12/12 |
| thecolyerteam.com | thecolyerteam-hub | 12/12 |
| apply.thecolyerteam.com | loan-application | 12/12 |
| fsbohelpcalifornia.com | fsbo-site | 12/12 (not deployed) |

All have: NMLS# 276626, DRE# 01842442, Broker Associate, Five M Realty Group Inc DRE# 02253302 (responsible broker), Equal Housing Lender/Opportunity, NMLS Consumer Access, Privacy w/GLBA+CCPA, Do Not Sell, TOS, Accessibility, judy@vip.thecolyerteam.com contact.

**Mike's DRE info (from public records):**
- Michael James Colyer, DRE# 01842442, License Type: BROKER
- Broker Associate for: Five M Realty Group, Inc., DRE# 02253302
- Address: 2214 Faraday Ave, Carlsbad, CA 92008
- NMLS# 276626

---

## 12. Beta Milestones

| Metric | Current | Target |
|--------|---------|--------|
| Sites scanned | 654 | 644 (done) |
| Sites AI-audited | 127 | 300-350 for private beta |
| Audit records | 1,000 | More is better |
| Scanner fixes | 17 deployed | Ongoing |
| v1.2 accuracy | 88.0% | 99% target |
| Next batch | 26 | Continue from here |

**Milestone 1 (private beta):** 300-350 validated sites, last 100 with ≤1 material error
**Milestone 2 (closed beta):** 10-15 external users, spot-checks reveal no systematic errors
**Milestone 3 (public):** 400-500 validated sites, 200-250 clean in a row

---

## 13. Important Gotchas

- **Python on Windows:** Use `py` (not `python3`)
- **Two scan endpoints:** `/scan` (frontend) and `/api/scan` (batch/API). Both must have all fixes.
- **audit_log has NO `notes` column.** Extra fields cause 400 errors on upload.
- **Scanner is deterministic — no AI/LLM.** The AI is only used during the audit cycle by Claude Code CLI.
- **WebFetch can't render JS.** AI audits sometimes return `inconclusive_js`. The scanner uses Playwright which CAN render JS, so `inconclusive_js` doesn't mean the scanner is wrong.
- **DRE lookup cache persists across requests** (FastAPI is long-running). Acceptable — DRE names rarely change.
- **Mac Mini .env must have API_KEYS.** If missing, all scans return 503.
- **Git on Mac Mini says "Already up to date" even after push.** This is normal if the prior pull already had the commit. The `cp` command still copies the latest file.

---

## 14. Chat History Summary

### Session 1 (2026-04-03)
- Read the codebase, understood scanner.py architecture
- Ran AI audits: batches 0-2 (18 sites)
- Found and fixed 5 bugs (EHL severity, placeholder emails, JS scroll, parked domains, CCPA external links)
- Deployed fixes to Mac Mini
- Started batch scanning from Windows (scan_to_supabase.py)

### Session 2 (2026-04-04)
- Discovered 383 scans failed due to missing API_KEYS after Mac Mini restart
- Fixed .env, re-ran scans
- Ran batches 3-5 (28 sites total)
- Analyzed accuracy: 77.3% overall
- Designed and implemented 6 major fixes (DRE lookup, EHO tightening, EHL SVG, platform waits, address regex, subpage diagnostic)
- Fixed critical bug: `/api/scan` endpoint missing all improvements
- Ran batches 6-11 with improved scanner
- Fixed additional issues: BRE prefix, CA DRE No. format, License ID format
- Made all Mike's client-facing sites compliant (6 repos, 13 files)
- Removed personal info from complywithjudy.com, added TOS
- Fixed quote-view.html for full CA lending compliance (with responsible broker)
- Ran batches 12-20, reaching 103 sites audited

### Session 3 (2026-04-05 to 2026-04-06)
- Designed v1.2 architectural improvements: entity classification, privacy subpage, vision analysis
- Implemented entity classifier (nonprofit, commercial_developer, property_manager, commercial_lender)
- Implemented privacy policy subpage scanning (follows privacy link, checks for CCPA)
- Added improved EHO DOM detection (font-icon CSS classes, rendered dimension check)
- Reverted vision/API approach — scanner stays deterministic, no AI integration
- Fixed phantom EHO from unrendered JS template images
- Tightened nonprofit classifier (was matching "loans for nonprofits")
- Added placeholder phone detection
- Ran batches 21-25 with v1.2 scanner
- Reached 127 sites audited, 1,000 audit records, 88% v1.2 accuracy
- **Next step: Continue with batch 26**
