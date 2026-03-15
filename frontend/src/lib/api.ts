import { supabase } from './supabase'

const API_URL = import.meta.env.VITE_API_URL

async function getAuthHeaders(): Promise<HeadersInit> {
  const { data: { session } } = await supabase.auth.getSession()
  const headers: HeadersInit = { 'Content-Type': 'application/json' }
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }
  return headers
}

export interface StartScanPayload {
  url: string
  profession: 'real_estate' | 'mortgage'
}

export interface StartScanResponse {
  scan_id: string
  status: string
  score?: number
  summary?: Record<string, number>
  checks?: RawCheck[]
  error?: string
}

export interface RawCheck {
  rule_id: string
  rule_name: string
  status: 'pass' | 'warning' | 'fail'
  message: string
  remediation: string | null
  evidence: string | null
  screenshot_required: boolean
}

// Transform raw Lambda check into the shape Results.tsx expects
function transformCheck(c: RawCheck, index: number) {
  return {
    id: c.rule_id || String(index),
    label: c.rule_name,
    category: getCategoryForRule(c.rule_id),
    status: c.status === 'warning' ? 'warn' : c.status,  // normalize 'warning' -> 'warn'
    detail: c.message,
    remediation: c.remediation ?? undefined,
    evidence: c.evidence ?? undefined,
    screenshot_required: c.screenshot_required,
    screenshot_url: undefined,
  }
}

function getCategoryForRule(ruleId: string): string {
  const map: Record<string, string> = {
    R01: 'License Disclosure',
    R02: 'License Disclosure',
    R03: 'License Disclosure',
    R04: 'Federal Regulations',
    R05: 'California State Law',
    R06: 'Privacy Compliance',
    R07: 'DFPI Requirements',
    R08: 'Fair Housing',
    R09: 'Advertising Rules',
    R10: 'Advertising Rules',
  }
  return map[ruleId] || 'General'
}

export async function startScan(payload: StartScanPayload): Promise<StartScanResponse> {
  const headers = await getAuthHeaders()
  const res = await fetch(`${API_URL}/scan`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })

  const data = await res.json().catch(() => ({}))

  if (!res.ok || data.error) {
    throw new Error(data.error || data.message || 'Failed to start scan.')
  }

  return data
}

export async function getScanResults(scanId: string) {
  // Read directly from Supabase — results are already stored there by Lambda
  const { data, error } = await supabase
    .from('scans')
    .select('*')
    .eq('id', scanId)
    .single()

  if (error || !data) {
    throw new Error(error?.message || 'Scan not found.')
  }

  // Normalize status: 'completed' -> 'complete'
  const status = data.status === 'completed' ? 'complete'
    : data.status === 'failed' ? 'error'
    : data.status

  // Extract checks from results JSON (Lambda stores as {checks: [...], score: N, ...})
  const rawResults = data.results
  let checks: ReturnType<typeof transformCheck>[] = []

  if (rawResults?.checks && Array.isArray(rawResults.checks)) {
    checks = rawResults.checks.map((c: RawCheck, i: number) => transformCheck(c, i))
  }

  return {
    id: data.id,
    url: data.url,
    profession: data.profession,
    status,
    score: data.score ?? rawResults?.score ?? 0,
    results: checks,
    created_at: data.created_at,
    tier: (data.tier ?? 'free') as 'free' | 'single' | 'fix_verify' | 'pro' | 'broker',
    summary: data.summary || rawResults?.summary,
  }
}

export async function getScanStatus(scanId: string): Promise<{ status: string; scan_id: string }> {
  const { data, error } = await supabase
    .from('scans')
    .select('id, status')
    .eq('id', scanId)
    .single()

  if (error || !data) throw new Error('Failed to fetch scan status.')

  return {
    scan_id: data.id,
    status: data.status === 'completed' ? 'complete' : data.status,
  }
}

export async function uploadScreenshot(scanId: string, checkId: string, file: File) {
  const { data: { session } } = await supabase.auth.getSession()
  const formData = new FormData()
  formData.append('file', file)
  formData.append('check_id', checkId)

  const headers: HeadersInit = {}
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }

  const res = await fetch(`${API_URL}/scan/${scanId}/screenshot`, {
    method: 'POST',
    headers,
    body: formData,
  })
  if (!res.ok) throw new Error('Screenshot upload failed.')
  return res.json()
}

export async function requestPdfReport(scanId: string): Promise<{ url: string }> {
  const headers = await getAuthHeaders()
  const res = await fetch(`${API_URL}/scan/${scanId}/report`, {
    method: 'POST',
    headers,
  })
  if (!res.ok) throw new Error('Failed to generate report.')
  return res.json()
}
