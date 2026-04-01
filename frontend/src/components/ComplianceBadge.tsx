import { useState } from 'react'

interface ComplianceBadgeProps {
  scanId: string
  score: number
  url: string
}

export default function ComplianceBadge({ scanId, score }: ComplianceBadgeProps) {
  const [copied, setCopied] = useState(false)

  const color = score >= 80 ? '#16a34a' : score >= 60 ? '#d97706' : '#dc2626'
  const label = score >= 80 ? 'Good Standing' : score >= 60 ? 'Needs Review' : 'Action Required'

  const embedCode = `<a href="https://complywithjudy.com/results/${scanId}" target="_blank" rel="noopener noreferrer" style="display:inline-flex;align-items:center;gap:10px;padding:10px 16px;background:#fff;border:1px solid #e5e7eb;border-radius:12px;text-decoration:none;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;box-shadow:0 1px 3px rgba(0,0,0,0.08)"><div style="width:36px;height:36px;border-radius:8px;background:${color};display:flex;align-items:center;justify-content:center;flex-shrink:0"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>${score >= 60 ? '<path d="M9 12l2 2 4-4"/>' : ''}</svg></div><div><div style="font-size:12px;font-weight:700;color:#1a2744;line-height:1.3">ComplyWithJudy</div><div style="font-size:11px;font-weight:600;color:${color};line-height:1.3">${score}/100 — ${label}</div><div style="font-size:10px;color:#9ca3af;line-height:1.3">Verified ${new Date().toLocaleDateString('en-US', { month: 'short', year: 'numeric' })}</div></div></a>`

  function handleCopy() {
    navigator.clipboard.writeText(embedCode)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
      <div className="flex items-start gap-4 mb-4">
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0"
          style={{ background: color }}
        >
          <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            {score >= 60 && <path d="M9 12l2 2 4-4" />}
          </svg>
        </div>
        <div>
          <h3 className="font-bold text-gray-900">Compliance Badge</h3>
          <p className="text-sm text-gray-500 mt-0.5">
            Add this badge to your website footer to show visitors your site has been scanned for California compliance.
          </p>
        </div>
      </div>

      {/* Badge preview */}
      <div className="bg-gray-50 rounded-xl p-4 mb-4 flex justify-center">
        <a
          href={`/results/${scanId}`}
          onClick={(e) => e.preventDefault()}
          className="inline-flex items-center gap-2.5 bg-white border border-gray-200 rounded-xl px-4 py-2.5 no-underline shadow-sm"
          style={{ textDecoration: 'none' }}
        >
          <div
            className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
            style={{ background: color }}
          >
            <svg className="w-[18px] h-[18px] text-white" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              {score >= 60 && <path d="M9 12l2 2 4-4" />}
            </svg>
          </div>
          <div>
            <div className="text-xs font-bold text-[#1a2744] leading-tight">ComplyWithJudy</div>
            <div className="text-[11px] font-semibold leading-tight" style={{ color }}>
              {score}/100 — {label}
            </div>
            <div className="text-[10px] text-gray-400 leading-tight">
              Verified {new Date().toLocaleDateString('en-US', { month: 'short', year: 'numeric' })}
            </div>
          </div>
        </a>
      </div>

      {/* Embed code */}
      <div className="relative">
        <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Embed Code
        </label>
        <div className="bg-gray-900 rounded-lg p-3 pr-20 overflow-x-auto">
          <code className="text-xs text-green-400 break-all whitespace-pre-wrap leading-relaxed">
            {embedCode.substring(0, 120)}...
          </code>
        </div>
        <button
          onClick={handleCopy}
          className={`absolute top-8 right-2 text-xs font-semibold px-3 py-1.5 rounded-md transition-colors ${
            copied
              ? 'bg-green-500 text-white'
              : 'bg-gray-700 text-gray-200 hover:bg-gray-600'
          }`}
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <p className="text-xs text-gray-400 mt-2">
        Paste this HTML into your website footer. The badge links back to your verified compliance report.
      </p>
    </div>
  )
}
