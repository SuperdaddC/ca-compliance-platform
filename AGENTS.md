# AGENTS.md

Guidance for Claude/agents working in this repo. Keep this file tight.
Update only when instructions, commands, assumptions, or fragile areas change.

## Stack & repo map

California real estate + mortgage lending compliance scanner.

- **Frontend**: React 18 + Vite + Tailwind, deployed to Netlify at complywithjudy.com. Supabase auth (email/password). Entry: `frontend/src/App.tsx`. Admin UI at `frontend/src/pages/admin/` (ReviewQueue, ReviewItem) with API client `frontend/src/lib/adminApi.ts`.
- **Backend**: FastAPI + Playwright, single file `backend/scanner.py` (~3300 lines). Runs on Mac Mini at Tailscale IP `100.96.2.29` under launchd service `com.complywithjudy.scanner`. Public URL: `https://scanner.complywithjudy.com`.
- **Database**: Supabase (Postgres). Key tables: `scans`, `audit_log` (historical AI audits, ~1268 rows), `review_queue`, `review_assets`, `user_profiles` (roles: user/admin). Storage bucket: `review-assets`.
- **Core flow**: User scans URL → Playwright renders → scanner.py runs ~20 rule checks + DRE/DFPI/NMLS lookups → result stored in `scans` → ambiguous rules auto-populate `review_queue` with a screenshot.

## Commands

- Frontend build: `cd frontend && npm run build`
- Frontend dev: `cd frontend && npm run dev`
- Backend deploy: `ssh thecolyerteam@100.96.2.29 "cd ~/complywithjudy-repo && git pull && launchctl stop com.complywithjudy.scanner && launchctl start com.complywithjudy.scanner"`
- Backend smoke-test: `curl -s -X POST "http://100.96.2.29:8000/api/scan" -H "X-API-Key: judy_prod_xK9mW2pL7vR4nQ8s" -H "Content-Type: application/json" -d '{"url":"https://example.com","profession":"real_estate"}'`
- Accuracy check: `rm -f tmp_rescan_results.json && python accuracy_check.py` (~40 min, 83 sites against `tmp_reviews.json`)
- Refresh review labels from Supabase: `python refresh_reviews.py` (rewrites `tmp_reviews.json`)
- Database migration: put SQL in `supabase/migrations/NNN_name.sql`, run manually in Supabase SQL editor (no CLI wired up)
- Admin queue API (production): `curl https://scanner.complywithjudy.com/admin/queue -H "X-API-Key: judy_prod_xK9mW2pL7vR4nQ8s"`

## Must not break

- `scanner.py` has two scan endpoints: `/scan` (legacy, internal) and `/api/scan` (public, API-key-gated). Most rule/classification logic is **duplicated across both** — changes must be applied to both or behavior diverges silently.
- `review_queue` has a partial unique index on `(site_url, rule_id) WHERE review_status IN ('pending','claimed')`. PATCHing a completed row to pending when a pending row already exists hits 409.
- `classify_entity()` drives which rules are skipped for lending_entity, commercial_developer, dfpi_lender, etc. Skips happen AFTER rule evaluation — changing classification is a high-blast-radius change.
- `DRE_LICENSE_RE` matches multiple formats (`DRE #`, `DRE No.`, `DRE Lic.`, `DRE ID`, `CalBRE`). If you narrow it, sites will silently fall out of `dre_license=pass`.
- `EQUAL_HOUSING_RE` matches both "Equal Housing Opportunity" AND "Equal Housing Lender" via optional capture group. Lenders rely on this; don't split the rule.
- `ab723_images` is **intentionally `status="info"` and excluded from scoring** via `scorable = [r for r in results if r.status not in ("skip", "info")]`. Do not revert to pass/warn/fail.
- Scanner runs from `~/complywithjudy-repo/backend/scanner.py` on the Mac Mini. The `~/complywithjudy-backend/scanner.py` path is a **stale copy** — updating it does nothing.

## Never without approval

- Changing `classify_entity()` logic or adding/removing entity types
- Adding/changing rules that affect the compliance score
- Destructive DB changes (dropping tables/columns, bulk deletes on `scans`, `audit_log`, `review_queue`)
- Auth changes (Supabase RLS policies, `user_profiles.role` handling, `verify_admin()` middleware)
- Broad refactors across `scanner.py` (it's a single 3300-line file — touch narrowly)
- Dependency swaps in `backend/requirements.txt` or `frontend/package.json`
- Breaking API changes to `/scan`, `/api/scan`, or any `/admin/queue/*` endpoint
- File moves that affect the Netlify build or the Mac Mini launchd service path

## Preferred patterns

- **Rules are pure functions** returning `RuleResult` dataclasses with id, name, status, description, detail, source_url, fix, regulation, webmaster_email, screenshot_required. Keep new rules in this shape.
- **Regex constants live at module level**, uppercase (e.g., `EQUAL_HOUSING_RE`, `DRE_LICENSE_RE`, `RESPONSIBLE_BROKER_RE`). Add new patterns alongside, don't inline.
- **DRE/DFPI/NMLS lookups** via `lookup_dre_info()`, `_try_dfpi_lookups()`, etc. Results cached in module-level dicts. Reuse these; don't add a second HTTP path.
- **Entity-type skips** apply in the `if entity_type == 'X':` blocks after rule evaluation. Add skips there, not inside individual rule functions.
- **Supabase writes** use `httpx.AsyncClient` with `SUPABASE_SERVICE_KEY`. There's a `service_role` RLS policy on every table — service key bypasses row auth.
- **Frontend API calls** go through `frontend/src/lib/adminApi.ts` with `getAuthHeaders()` adding the Supabase session token. Don't bypass.
- **Script outputs** go to `tmp_*.json` / `tmp_*.txt` at repo root (these are in `.gitignore`). Keep short-lived scratch work there.

## Open issues / fragile areas

- **Review labels pre-dating 2026-04-15 may be wrong**, especially for `equal_housing` and `responsible_broker` on lending sites. Many were marked `false_positive`/`not_applicable` when scanner was actually correct. When a metric regresses, check labels before changing code.
- **EHO detection known gaps**: image-only logos with no alt text (needs OCR), EHO baked into composite footer images, CSS font-icon-only implementations. `hillhurstmortgage.com` is the canonical example.
- **Playwright scan-to-scan variability**: JS-rendered DRE numbers, networkidle timeouts, and deferred content can appear in one scan and not the next. Flaky FNs on lending sites are often this, not a regex bug.
- **Responsible broker strict vs. loose**: regulation §2773.1 requires broker name; DRE guidelines suggest name + DRE#. Scanner does strict (name + DRE# pattern). Sites showing only the broker name will still `warn`. Human reviewers are split; this is the irreducible FP floor (~5-6 sites on the 83-site test set).
- **Dual scan endpoints** (`/scan` + `/api/scan`): any rule/classification change must be applied to both. See `Must not break`.
- **DFPI SearchStax API is unreliable** — timeouts and 500s are common. `_try_dfpi_lookups()` fails open (returns False). Don't treat DFPI non-confirmation as signal.
- **ab723_images intentionally "info"**: accuracy_check.py counts these as FNs in the raw number. Real-world accuracy is typically ~6-9 points higher than the raw number after excluding ab723 artifacts.
- **Mac Mini SSH user is `thecolyerteam`** (not `mike`, `mcolyer`, etc.). Repo location: `~/complywithjudy-repo`. Backend runs from there, not `~/complywithjudy-backend` (stale copy).
