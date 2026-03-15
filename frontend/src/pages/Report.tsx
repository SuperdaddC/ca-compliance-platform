import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getScanResults } from '../lib/api'

interface Check {
  id: string
  label: string
  category: string
  status: 'pass' | 'warn' | 'fail' | 'na'
  detail: string
  remediation?: string
}

interface ScanData {
  id: string
  url: string
  profession: string
  score: number
  results: Check[]
  created_at: string
  tier?: string
}

function isPaid(tier: string | undefined) {
  return tier === 'single' || tier === 'fix_verify' || tier === 'pro' || tier === 'broker'
}

const STATUS_LABELS: Record<string, string> = {
  pass: 'PASS',
  warn: 'WARNING',
  fail: 'FAILED',
  na: 'N/A',
}

const STATUS_COLORS: Record<string, string> = {
  pass: '#16a34a',
  warn: '#d97706',
  fail: '#dc2626',
  na:   '#9ca3af',
}

function ScoreRing({ score }: { score: number }) {
  const r = 54
  const circ = 2 * Math.PI * r
  const fill = circ * (score / 100)
  const color = score >= 80 ? '#16a34a' : score >= 60 ? '#d97706' : '#dc2626'
  const label = score >= 80 ? 'Good Standing' : score >= 60 ? 'Needs Attention' : 'Action Required'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
      <svg width="130" height="130" viewBox="0 0 130 130">
        <circle cx="65" cy="65" r={r} fill="none" stroke="#e5e7eb" strokeWidth="10" />
        <circle
          cx="65" cy="65" r={r}
          fill="none"
          stroke={color}
          strokeWidth="10"
          strokeDasharray={`${fill} ${circ}`}
          strokeLinecap="round"
          transform="rotate(-90 65 65)"
        />
        <text x="65" y="60" textAnchor="middle" fontSize="28" fontWeight="700" fill={color}>{score}</text>
        <text x="65" y="78" textAnchor="middle" fontSize="11" fill="#6b7280">/100</text>
      </svg>
      <span style={{ fontSize: 13, fontWeight: 600, color }}>{label}</span>
    </div>
  )
}

export default function Report() {
  const { scanId } = useParams<{ scanId: string }>()
  const [scan, setScan] = useState<ScanData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!scanId) return
    getScanResults(scanId)
      .then((data) => setScan(data as ScanData))
      .catch((e) => setError(e.message))
  }, [scanId])

  useEffect(() => {
    if (scan) {
      document.title = `Compliance Report — ${scan.url}`
      // Small delay so styles render before print dialog
      setTimeout(() => window.print(), 800)
    }
  }, [scan])

  if (error) {
    return <div style={{ padding: 40, fontFamily: 'sans-serif', color: '#dc2626' }}>Error: {error}</div>
  }
  if (!scan) {
    return (
      <div style={{ padding: 40, fontFamily: 'sans-serif', color: '#6b7280', textAlign: 'center' }}>
        Loading report…
      </div>
    )
  }

  const checks = scan.results ?? []
  const passed   = checks.filter(c => c.status === 'pass').length
  const warnings = checks.filter(c => c.status === 'warn').length
  const failed   = checks.filter(c => c.status === 'fail').length
  const paidUser = isPaid(scan.tier)
  const categories = Array.from(new Set(checks.map(c => c.category))).sort()
  const date = new Date(scan.created_at).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })
  const profLabel = scan.profession === 'lending' ? 'Mortgage / Lending' : 'Real Estate'

  return (
    <>
      {/* Print-only styles */}
      <style>{`
        @page { size: letter; margin: 0.75in 0.75in 0.75in 0.75in; }
        @media print {
          .no-print { display: none !important; }
          body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
        }
        body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #fff; }
        * { box-sizing: border-box; }
      `}</style>

      <div style={{ maxWidth: 780, margin: '0 auto', padding: '32px 24px', color: '#111827' }}>

        {/* Print button (hidden in print) */}
        <div className="no-print" style={{ marginBottom: 24, display: 'flex', gap: 12 }}>
          <button
            onClick={() => window.print()}
            style={{ background: '#2563eb', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 20px', fontWeight: 600, fontSize: 14, cursor: 'pointer' }}
          >
            🖨️ Print / Save as PDF
          </button>
          <button
            onClick={() => window.close()}
            style={{ background: '#f3f4f6', color: '#374151', border: '1px solid #d1d5db', borderRadius: 8, padding: '10px 20px', fontWeight: 600, fontSize: 14, cursor: 'pointer' }}
          >
            Close
          </button>
        </div>

        {/* Header */}
        <div style={{ borderBottom: '3px solid #2563eb', paddingBottom: 20, marginBottom: 28 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#2563eb', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 4 }}>
                complywithjudy.com
              </div>
              <h1 style={{ margin: '0 0 6px', fontSize: 26, fontWeight: 800, color: '#111827' }}>
                California Compliance Report
              </h1>
              <p style={{ margin: 0, fontSize: 13, color: '#6b7280' }}>
                {profLabel} &nbsp;·&nbsp; {date}
              </p>
              <p style={{ margin: '6px 0 0', fontSize: 14, color: '#374151', fontWeight: 500, wordBreak: 'break-all' }}>
                {scan.url}
              </p>
            </div>
            <ScoreRing score={scan.score} />
          </div>
        </div>

        {/* Summary row */}
        <div style={{ display: 'flex', gap: 14, marginBottom: 28 }}>
          {[
            { label: 'Passed',   count: passed,   bg: '#f0fdf4', border: '#bbf7d0', color: '#16a34a' },
            { label: 'Warnings', count: warnings, bg: '#fffbeb', border: '#fde68a', color: '#d97706' },
            { label: 'Failed',   count: failed,   bg: '#fef2f2', border: '#fecaca', color: '#dc2626' },
          ].map(({ label, count, bg, border, color }) => (
            <div key={label} style={{ flex: 1, background: bg, border: `1px solid ${border}`, borderRadius: 10, padding: '14px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 28, fontWeight: 800, color }}>{count}</div>
              <div style={{ fontSize: 12, fontWeight: 600, color, marginTop: 2 }}>{label}</div>
            </div>
          ))}
        </div>

        {/* Disclaimer */}
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, padding: '12px 16px', marginBottom: 28, fontSize: 12, color: '#92400e', lineHeight: 1.5 }}>
          <strong>Important:</strong> This report checks for common compliance indicators only. A passing score is not a legal determination of compliance.
          Regulations have proximity, prominence, and content requirements that automated scanning cannot fully verify. Consult a qualified compliance attorney before relying on these results.
        </div>

        {/* Results by category */}
        {categories.map(cat => {
          const catChecks = checks.filter(c => c.category === cat)
          return (
            <div key={cat} style={{ marginBottom: 28 }}>
              <h2 style={{ fontSize: 13, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.08em', margin: '0 0 10px', borderBottom: '1px solid #e5e7eb', paddingBottom: 6 }}>
                {cat}
              </h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {catChecks.map(check => {
                  const color = STATUS_COLORS[check.status] ?? '#9ca3af'
                  const bgMap: Record<string, string> = { pass: '#f0fdf4', warn: '#fffbeb', fail: '#fef2f2', na: '#f9fafb' }
                  const borderMap: Record<string, string> = { pass: '#bbf7d0', warn: '#fde68a', fail: '#fecaca', na: '#e5e7eb' }
                  return (
                    <div key={check.id} style={{ background: bgMap[check.status] ?? '#f9fafb', border: `1px solid ${borderMap[check.status] ?? '#e5e7eb'}`, borderRadius: 8, padding: '12px 14px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                        <span style={{ fontSize: 14, fontWeight: 600, color: '#111827' }}>{check.label}</span>
                        <span style={{ fontSize: 11, fontWeight: 700, color, background: '#fff', border: `1px solid ${color}`, borderRadius: 99, padding: '2px 8px', whiteSpace: 'nowrap', flexShrink: 0 }}>
                          {STATUS_LABELS[check.status] ?? check.status.toUpperCase()}
                        </span>
                      </div>
                      <p style={{ margin: '6px 0 0', fontSize: 13, color: '#4b5563', lineHeight: 1.5 }}>{check.detail}</p>
                      {check.status === 'fail' && check.remediation && (
                        paidUser ? (
                          <div style={{ marginTop: 10, background: '#fff', border: '1px solid #fecaca', borderRadius: 6, padding: '10px 12px' }}>
                            <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>How to fix this</div>
                            <p style={{ margin: 0, fontSize: 13, color: '#374151', lineHeight: 1.5 }}>{check.remediation}</p>
                          </div>
                        ) : (
                          <div style={{ marginTop: 10, background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 6, padding: '10px 12px', fontSize: 13, color: '#9ca3af', fontStyle: 'italic' }}>
                            Fix instructions available with paid report — visit complywithjudy.com to upgrade.
                          </div>
                        )
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}

        {/* Footer */}
        <div style={{ borderTop: '1px solid #e5e7eb', marginTop: 32, paddingTop: 16, fontSize: 11, color: '#9ca3af', display: 'flex', justifyContent: 'space-between' }}>
          <span>Generated by complywithjudy.com</span>
          <span>{date}</span>
        </div>

      </div>
    </>
  )
}
