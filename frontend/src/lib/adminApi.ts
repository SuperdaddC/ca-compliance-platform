import { supabase } from './supabase'

const SCANNER_URL = import.meta.env.VITE_SCANNER_URL || 'https://scanner.complywithjudy.com'

async function getAuthHeaders(): Promise<HeadersInit> {
  const { data: { session } } = await supabase.auth.getSession()
  const headers: HeadersInit = { 'Content-Type': 'application/json' }
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }
  return headers
}

// Types
export interface ReviewItem {
  id: string
  scan_id: string | null
  site_url: string
  page_url: string | null
  profession: string
  entity_type: string
  score: number | null
  rule_id: string
  rule_name: string
  scanner_status: string
  scanner_detail: string | null
  scanner_evidence: string | null
  scanner_version: string | null
  rule_version: string | null
  review_status: string
  claimed_by: string | null
  claimed_at: string | null
  decision: string | null
  reviewer_id: string | null
  reviewer_note: string | null
  bug_tag: string | null
  broker_info: Record<string, string> | null
  source: string
  created_at: string
  reviewed_at: string | null
  updated_at: string
}

export interface ReviewAsset {
  id: string
  review_item_id: string
  asset_type: string
  storage_path: string
  filename: string | null
  mime_type: string | null
  uploaded_by: string | null
  caption: string | null
  created_at: string
}

export interface QueueStats {
  review_status: string
  total: number
  confirmed: number
  false_positives: number
  bugs_found: number
  not_applicable: number
  needs_rescan: number
}

export interface QueueListResponse {
  items: ReviewItem[]
  total: number
  page: number
  per_page: number
}

export interface QueueDetailResponse {
  item: ReviewItem
  assets: ReviewAsset[]
  scan_context: any | null
}

export interface QueueFilters {
  review_status?: string
  rule_id?: string
  profession?: string
  bug_tag?: string
  claimed_by?: string
  page?: number
  per_page?: number
}

// API functions

export async function getReviewQueue(filters: QueueFilters = {}): Promise<QueueListResponse> {
  const params = new URLSearchParams()
  if (filters.review_status !== undefined) params.set('review_status', filters.review_status)
  if (filters.rule_id) params.set('rule_id', filters.rule_id)
  if (filters.profession) params.set('profession', filters.profession)
  if (filters.bug_tag) params.set('bug_tag', filters.bug_tag)
  if (filters.claimed_by) params.set('claimed_by', filters.claimed_by)
  params.set('page', String(filters.page ?? 0))
  params.set('per_page', String(filters.per_page ?? 50))

  const headers = await getAuthHeaders()
  const r = await fetch(`${SCANNER_URL}/admin/queue?${params}`, { headers })
  if (!r.ok) throw new Error(`Queue list failed: ${r.status}`)
  return r.json()
}

export async function getQueueStats(): Promise<QueueStats[]> {
  const headers = await getAuthHeaders()
  const r = await fetch(`${SCANNER_URL}/admin/queue/stats`, { headers })
  if (!r.ok) throw new Error(`Stats failed: ${r.status}`)
  return r.json()
}

export async function getQueueItem(id: string): Promise<QueueDetailResponse> {
  const headers = await getAuthHeaders()
  const r = await fetch(`${SCANNER_URL}/admin/queue/${id}`, { headers })
  if (!r.ok) throw new Error(`Item detail failed: ${r.status}`)
  return r.json()
}

export async function submitDecision(id: string, decision: string, note?: string, bugTag?: string, brokerInfo?: Record<string, string>) {
  const headers = await getAuthHeaders()
  const payload: any = { decision, reviewer_note: note || null, bug_tag: bugTag || null }
  if (brokerInfo && Object.values(brokerInfo).some(v => v)) {
    payload.broker_info = brokerInfo
  }
  const r = await fetch(`${SCANNER_URL}/admin/queue/${id}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(payload),
  })
  if (!r.ok) throw new Error(`Decision failed: ${r.status}`)
  return r.json()
}

export async function claimItem(id: string) {
  const headers = await getAuthHeaders()
  const r = await fetch(`${SCANNER_URL}/admin/queue/${id}/claim`, { method: 'PATCH', headers })
  if (!r.ok) throw new Error(`Claim failed: ${r.status}`)
  return r.json()
}

export async function releaseItem(id: string) {
  const headers = await getAuthHeaders()
  const r = await fetch(`${SCANNER_URL}/admin/queue/${id}/release`, { method: 'PATCH', headers })
  if (!r.ok) throw new Error(`Release failed: ${r.status}`)
  return r.json()
}

export async function bulkDecide(itemIds: string[], decision: string, note?: string, bugTag?: string) {
  const headers = await getAuthHeaders()
  const r = await fetch(`${SCANNER_URL}/admin/queue/bulk`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ item_ids: itemIds, decision, reviewer_note: note, bug_tag: bugTag }),
  })
  if (!r.ok) throw new Error(`Bulk decide failed: ${r.status}`)
  return r.json()
}

export async function uploadAsset(itemId: string, file: File) {
  const { data: { session } } = await supabase.auth.getSession()
  const formData = new FormData()
  formData.append('file', file)
  const r = await fetch(`${SCANNER_URL}/admin/queue/${itemId}/assets`, {
    method: 'POST',
    headers: session?.access_token ? { 'Authorization': `Bearer ${session.access_token}` } : {},
    body: formData,
  })
  if (!r.ok) throw new Error(`Upload failed: ${r.status}`)
  return r.json()
}

// Storage URL helper
export function getAssetUrl(storagePath: string): string {
  const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
  return `${supabaseUrl}/storage/v1/object/public/${storagePath}`
}

// Decision labels
export const DECISIONS = [
  { key: 'confirmed_violation', label: 'Confirmed', shortcut: '1', color: 'red' },
  { key: 'false_positive', label: 'False Pos', shortcut: '2', color: 'green' },
  { key: 'not_applicable', label: 'N/A', shortcut: '3', color: 'gray' },
  { key: 'needs_rescan', label: 'Rescan', shortcut: '4', color: 'blue' },
  { key: 'scanner_bug', label: 'Bug', shortcut: '5', color: 'purple' },
] as const

// Bug tags — predefined categories for scanner improvement tracking
export const BUG_TAGS = [
  { key: 'eho_image_no_alt', label: 'EHO Image (no alt)', tooltip: 'Equal Housing logo is an image with no alt text or a UUID filename. Scanner can\'t detect it without text.' },
  { key: 'eho_svg', label: 'EHO in SVG', tooltip: 'Equal Housing logo rendered as an SVG with no text content. Scanner checks SVG textContent but this one has only paths.' },
  { key: 'eho_font_icon', label: 'EHO Font Icon', tooltip: 'Equal Housing logo is a CSS font icon (e.g., ssi-eho class). Scanner may miss these.' },
  { key: 'eho_vs_ehl', label: 'EHO vs EHL', tooltip: 'Site shows "Equal Housing Opportunity" but as a lender should show "Equal Housing Lender". Scanner didn\'t distinguish.' },
  { key: 'js_rendering', label: 'JS Rendering', tooltip: 'Content is in a JS-rendered footer that Playwright didn\'t fully capture. Common on KW, Compass, Wix, Squarespace.' },
  { key: 'subpage_content', label: 'Subpage Content', tooltip: 'The compliance element exists on a subpage (e.g., /privacy, /contact, /about) but scanner only checked the homepage.' },
  { key: 'redirect_issue', label: 'Redirect Issue', tooltip: 'Site redirected to a different URL and scanner evaluated the wrong page or lost content during redirect.' },
  { key: 'entity_misclass', label: 'Entity Misclass', tooltip: 'Scanner classified this entity incorrectly (e.g., nonprofit flagged as brokerage, or commercial lender flagged for residential rules).' },
  { key: 'regex_miss', label: 'Regex Miss', tooltip: 'The compliance text/number IS on the page but the scanner\'s regex pattern didn\'t match it. Non-standard format.' },
  { key: 'placeholder_data', label: 'Placeholder Data', tooltip: 'Scanner detected placeholder/template content (e.g., example@domain.com, (123)456-7890) as real data.' },
  { key: 'parked_domain', label: 'Parked/Dead Domain', tooltip: 'Site is parked, suspended, or redirects to an unrelated business. Should not be scored.' },
  { key: 'dre_lookup_error', label: 'DRE Lookup Error', tooltip: 'The DRE public lookup returned wrong info, timed out, or misidentified the license type.' },
  { key: 'ccpa_on_subpage', label: 'CCPA on Subpage', tooltip: 'Privacy policy has CCPA content but scanner didn\'t find it because the privacy page link wasn\'t followed or rendered.' },
  { key: 'false_trigger', label: 'False TILA Trigger', tooltip: 'Scanner flagged TILA/Reg Z but the "triggering terms" are generic marketing language, not actual rate/payment ads.' },
  { key: 'rule_not_applicable', label: 'Rule N/A', tooltip: 'Rule doesn\'t apply to this page (e.g., AB 723 flagged but no property images exist, or DRE check on DFPI-only lender).' },
  { key: 'other', label: 'Other', tooltip: 'Doesn\'t fit any predefined category. Add details in the note field.' },
] as const

// Reviewer hints per rule_id
export const REVIEWER_HINTS: Record<string, string> = {
  equal_housing: "Look for EHO logo in footer \u2014 may be SVG, image with no alt, or font-icon (ssi-eho class).",
  equal_housing_lender: "Lenders need 'Equal Housing Lender' specifically, not just 'Opportunity'. Check footer text vs logo.",
  ccpa_privacy: "Check for site's OWN privacy link, not Google reCAPTCHA. May be on /privacy subpage. Look for 'Do Not Sell' link.",
  responsible_broker: "If DRE# found, check DRE public records. Corporation/Broker = auto-pass. Salesperson = needs broker disclosure.",
  tila_apr: "Look for specific rates (X.XX%), payments ($X,XXX/mo), or loan terms (30-year fixed). If found, APR must be nearby.",
  dre_license: "May be in JS-rendered footer. Common on KW, Compass, eXp. Try 'License ID:', 'CalBRE#', 'BRE #' formats.",
  dre_license_mlo: "Check if DFPI-licensed (not DRE). DFPI lenders don't need DRE MLO endorsement.",
  contact_info: "Scanner only checks scanned URL. Contact may be on homepage or /contact page.",
  physical_address: "Must be a street address with number + suffix (Ave, St, Blvd). City-only doesn't count.",
  safe_nmls: "NMLS may be in JS-rendered footer. Check for NMLS #, NMLSR, or just a 6-digit number near 'NMLS'.",
  ab723_images: "AB 723 applies to listing photos that are virtually staged or digitally altered. Look for disclosure near property images.",
  dfpi_prohibited: "Check for 'guaranteed approval', 'no credit check', 'instant approval'. Conditional offers with disclaimers are usually OK.",
  nmls_consumer_access: "Look for a clickable link to nmlsconsumeraccess.org. Just displaying the NMLS number is not enough.",
  ada_accessibility: "Look for accessibility widget (UserWay, accessiBe) or /accessibility page link in footer.",
  team_advertising: "Team name must include broker affiliation. DRE# of broker must be as prominent as team name.",
  ai_crawler_access: "Check robots.txt for blocks on GPTBot, ClaudeBot, CCBot, Google-Extended.",
}
