import { supabase } from './supabase'

const SCANNER_URL = import.meta.env.VITE_SCANNER_URL || 'https://brvhtwhiy5.execute-api.us-west-2.amazonaws.com/dev'
const STRIPE_FN = import.meta.env.VITE_STRIPE_FUNCTION_URL || '/.netlify/functions/stripe'

async function getAuthHeaders(): Promise<HeadersInit> {
  const { data: { session } } = await supabase.auth.getSession()
  const headers: HeadersInit = { 'Content-Type': 'application/json' }
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }
  return headers
}

// ----------------------------------------------------------------
// Types
// ----------------------------------------------------------------
export interface ScanRequest {
  url: string
  email: string
  profession: 'realestate' | 'lending'
  scan_id?: string
  user_id?: string
}

export interface ScanResult {
  scan_id: string
  score: number
  url: string
  profession: string
  elapsed_seconds: number
  is_free_scan: boolean
  status: 'completed' | 'failed' | 'running' | 'pending'
  plan?: string
  error_type?: string
  error_message?: string
  checks: CheckResult[]
}

export interface CheckResult {
  id: string
  name: string
  status: 'pass' | 'fail' | 'warn' | 'skip'
  description: string
  detail?: string
  source_url?: string
  fix?: string | null
  regulation?: string
  webmaster_email?: string
}

export type PlanKey = 'starter' | 'professional' | 'broker' | 'single'

// ----------------------------------------------------------------
// Core scan
// ----------------------------------------------------------------
export async function scanWebsite(req: ScanRequest): Promise<ScanResult> {
  const headers = await getAuthHeaders()
  const res = await fetch(`${SCANNER_URL}/scan`, {
    method: 'POST',
    headers,
    body: JSON.stringify(req),
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    const error = Object.assign(new Error('Scan failed'), { response: { status: res.status, data: err } })
    throw error
  }

  const data = await res.json()

  // Capture email lead in background (non-blocking)
  captureEmailLead(req.email, 'scan').catch(() => {})

  return data
}

// ----------------------------------------------------------------
// Retry a failed scan
// ----------------------------------------------------------------
export async function retryScan(scanId: string): Promise<ScanResult> {
  const headers = await getAuthHeaders()
  const res = await fetch(`${SCANNER_URL}/scan/retry/${scanId}`, {
    method: 'POST',
    headers,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw Object.assign(new Error('Retry failed'), { response: { status: res.status, data: err } })
  }
  return res.json()
}

// ----------------------------------------------------------------
// Get scan result by ID (for direct link / email click)
// ----------------------------------------------------------------
export async function getScanResult(scanId: string): Promise<ScanResult> {
  const res = await fetch(`${SCANNER_URL}/scan/${scanId}`)
  if (!res.ok) throw new Error('Result not found')
  return res.json()
}

// ----------------------------------------------------------------
// Legacy: read scan from Supabase (used by Report.tsx)
// ----------------------------------------------------------------
export async function getScanResults(scanId: string) {
  const { data, error } = await supabase
    .from('scans')
    .select('*')
    .eq('id', scanId)
    .single()

  if (error || !data) {
    throw new Error(error?.message || 'Scan not found.')
  }

  const status = data.status === 'completed' ? 'complete'
    : data.status === 'failed' ? 'error'
    : data.status

  const rawResults = data.results ?? data.result
  let checks: { id: string; label: string; category: string; status: string; detail: string; remediation?: string }[] = []

  if (rawResults?.checks && Array.isArray(rawResults.checks)) {
    checks = rawResults.checks.map((c: CheckResult, i: number) => ({
      id: c.id || String(i),
      label: c.name,
      category: 'General',
      status: c.status === 'skip' ? 'na' : c.status,
      detail: c.description,
      remediation: c.fix ?? undefined,
    }))
  }

  return {
    id: data.id,
    url: data.url,
    profession: data.profession,
    status,
    score: data.score ?? rawResults?.score ?? 0,
    results: checks,
    created_at: data.created_at,
    tier: (data.tier ?? 'free') as string,
    summary: data.summary || rawResults?.summary,
  }
}

// ----------------------------------------------------------------
// Screenshot upload (kept for CheckResult component)
// ----------------------------------------------------------------
export async function uploadScreenshot(scanId: string, checkId: string, file: File) {
  const { data: { session } } = await supabase.auth.getSession()
  const formData = new FormData()
  formData.append('file', file)
  formData.append('check_id', checkId)

  const headers: HeadersInit = {}
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }

  const res = await fetch(`${SCANNER_URL}/scan/${scanId}/screenshot`, {
    method: 'POST',
    headers,
    body: formData,
  })
  if (!res.ok) throw new Error('Screenshot upload failed.')
  return res.json()
}

// ----------------------------------------------------------------
// PDF report (opens print-friendly page)
// ----------------------------------------------------------------
export async function requestPdfReport(scanId: string): Promise<{ url: string }> {
  const url = `/report/${scanId}`
  window.open(url, '_blank')
  return { url }
}

// ----------------------------------------------------------------
// Stripe checkout
// ----------------------------------------------------------------
export async function createCheckout(params: {
  plan: PlanKey
  email?: string
  userId?: string
  scanId?: string
}): Promise<string> {
  const res = await fetch(`${STRIPE_FN}/create-checkout`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...params,
      successUrl: `${window.location.origin}/checkout/success`,
      cancelUrl: `${window.location.origin}/#pricing`,
    }),
  })

  if (!res.ok) throw new Error('Could not start checkout')
  const { url } = await res.json()
  return url
}

// ----------------------------------------------------------------
// Email lead capture
// ----------------------------------------------------------------
export async function captureEmailLead(email: string, source: string = 'scan'): Promise<void> {
  await fetch(`${STRIPE_FN}/capture-lead`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, source }),
  })
}

// ----------------------------------------------------------------
// Pricing catalogue (client-side reference)
// ----------------------------------------------------------------
export const PLANS = {
  starter: {
    name: 'Starter',
    price: '$29.99',
    period: '/year',
    scans: '5 scans',
    description: 'Perfect for solo agents — annual compliance check',
    highlight: false,
  },
  professional: {
    name: 'Professional',
    price: '$79.99',
    period: '/year',
    scans: '25 scans',
    description: 'Active agents & team leads who update their sites regularly',
    highlight: true,
  },
  broker: {
    name: 'Broker / Team',
    price: '$199.99',
    period: '/year',
    scans: 'Unlimited · 10 domains',
    description: 'Brokerages managing multiple agents and websites',
    highlight: false,
  },
  single: {
    name: 'Single Scan',
    price: '$19.99',
    period: 'one time',
    scans: '1 scan with full fix report',
    description: 'Try it before you subscribe',
    highlight: false,
  },
} satisfies Record<PlanKey, object>
