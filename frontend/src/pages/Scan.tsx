import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Navbar from '../components/Navbar'
import { useAuth } from '../App'
import { scanWebsite } from '../lib/api'

type Profession = 'realestate' | 'lending'

const SOCIAL_PROOF = [
  { stat: '20+', label: 'scans run' },
  { stat: '40+', label: 'checks performed' },
  { stat: '28 yrs', label: 'DRE experience behind every rule' },
]

const PROGRESS_MESSAGES = [
  'Fetching your website…',
  'Checking disclosure requirements…',
  'Scanning for license number display…',
  'Reviewing Equal Housing compliance…',
  'Checking NMLS requirements…',
  'Analyzing team advertising rules…',
  'Verifying contact information…',
  'Calculating your compliance score…',
]

const ERROR_LABELS: Record<string, string> = {
  timeout: '⏱ Site took too long',
  blocked: '🚧 Site blocked the scan',
  dns_fail: '🔍 Domain not found',
  ssl_error: '🔒 SSL certificate issue',
  limit_reached: '🔒 Free scan used',
}

export default function Scan() {
  const navigate = useNavigate()
  const { user, role } = useAuth()
  const [url, setUrl] = useState('')
  const [email, setEmail] = useState('')
  const [profession, setProfession] = useState<Profession>('realestate')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<{ message: string; type?: string } | null>(null)
  const [progressIndex, setProgressIndex] = useState(0)

  // Courtesy scan (admin only)
  const isAdmin = role === 'admin'
  const [courtesyMode, setCourtesyMode] = useState(false)
  const [courtesyEmail, setCourtesyEmail] = useState('')
  const [courtesyName, setCourtesyName] = useState('')

  function normalizeUrl(raw: string): string {
    const trimmed = raw.trim()
    if (!trimmed) return ''
    if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
      return trimmed
    }
    return `https://${trimmed}`
  }

  function validateUrl(raw: string): string | null {
    try {
      const u = new URL(normalizeUrl(raw))
      if (!u.hostname.includes('.')) return 'Please enter a valid website URL (e.g. janesmith.com)'
      return null
    } catch {
      return 'Please enter a valid website URL (e.g. janesmith.com)'
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const urlError = validateUrl(url)
    if (urlError) {
      setError({ message: urlError })
      return
    }
    if (!email.trim()) return

    setLoading(true)
    setError(null)

    let idx = 0
    const interval = setInterval(() => {
      idx = (idx + 1) % PROGRESS_MESSAGES.length
      setProgressIndex(idx)
    }, 2000)

    try {
      const result = await scanWebsite({
        url: normalizeUrl(url),
        email: email.trim(),
        profession,
        ...(user ? { user_id: user.id } : {}),
        ...(courtesyMode && courtesyEmail.trim() ? {
          courtesy_to: courtesyEmail.trim(),
          courtesy_name: courtesyName.trim(),
        } : {}),
      })
      clearInterval(interval)
      navigate(`/results/${result.scan_id}`, { state: { result } })
    } catch (err: unknown) {
      clearInterval(interval)
      const e = err as { response?: { status?: number; data?: { detail?: { message?: string; error_type?: string } } } }
      const detail = e?.response?.data?.detail
      if (detail && typeof detail === 'object') {
        setError({ message: detail.message ?? 'Scan failed.', type: detail.error_type })
      } else if (e?.response?.status === 429) {
        setError({
          message: "You've already used your free scan from this device. Create an account or upgrade to run more scans.",
          type: 'limit_reached',
        })
      } else {
        setError({ message: 'Something went wrong. Please try again.' })
      }
      setLoading(false)
    }
  }

  const professionChecks = profession === 'realestate'
    ? 'DRE license display, responsible broker, Equal Housing, AB 723 image disclosure, CCPA, team advertising rules'
    : 'NMLS disclosure, TILA/Reg Z APR proximity, Equal Housing Lender, DFPI prohibited claims, CCPA'

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />

      <div className="max-w-2xl mx-auto px-4 sm:px-6 py-12 sm:py-16">
        {!loading ? (
          <>
            <div className="text-center mb-8">
              <h1 className="text-3xl font-bold text-gray-900 mb-2">
                Scan your website
              </h1>
              <p className="text-gray-500">
                Check your site against California DRE and NMLS requirements in under 60 seconds.
              </p>
            </div>

            {/* Social proof strip */}
            <div className="flex justify-center gap-8 mb-8 flex-wrap">
              {SOCIAL_PROOF.map(({ stat, label }) => (
                <div key={label} className="text-center">
                  <div className="text-xl font-bold text-brand-gold">{stat}</div>
                  <div className="text-xs text-gray-500">{label}</div>
                </div>
              ))}
            </div>

            <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-8">
              <form onSubmit={handleSubmit} className="space-y-6">
                {/* URL Input */}
                <div>
                  <label htmlFor="url" className="block text-sm font-semibold text-gray-700 mb-2">
                    Your website URL
                  </label>
                  <div className="relative">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                      <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
                      </svg>
                    </div>
                    <input
                      id="url"
                      type="text"
                      value={url}
                      onChange={(e) => {
                        setUrl(e.target.value)
                        setError(null)
                      }}
                      placeholder="yoursite.com"
                      className="w-full pl-10 pr-4 py-3 rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-brand-gold focus:border-transparent text-gray-900 placeholder-gray-400"
                      required
                      autoFocus
                    />
                  </div>
                </div>

                {/* Email Input (required) */}
                <div>
                  <label htmlFor="email" className="block text-sm font-semibold text-gray-700 mb-2">
                    Email for your results
                  </label>
                  <input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@yourbrokerage.com"
                    className="w-full px-4 py-3 rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-brand-gold focus:border-transparent text-gray-900 placeholder-gray-400"
                    required
                    autoComplete="email"
                  />
                  <p className="mt-1.5 text-xs text-gray-400">We'll email your results. No spam — ever.</p>
                </div>

                {/* Profession Selector */}
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">
                    Your profession
                  </label>
                  <div className="grid grid-cols-2 gap-3">
                    {([
                      { value: 'realestate' as Profession, label: 'Real Estate Agent / Broker', icon: '🏠' },
                      { value: 'lending' as Profession, label: 'Mortgage Loan Officer', icon: '📄' },
                    ]).map((opt) => (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => setProfession(opt.value)}
                        className={`flex flex-col items-center gap-2 p-4 rounded-xl border-2 transition-all text-center ${
                          profession === opt.value
                            ? 'border-brand-gold bg-amber-50 text-brand-blue'
                            : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                        }`}
                      >
                        <span className="text-2xl">{opt.icon}</span>
                        <span className="text-sm font-medium leading-tight">{opt.label}</span>
                      </button>
                    ))}
                  </div>
                </div>

                {/* Courtesy scan toggle (admin only) */}
                {isAdmin && (
                  <div className="border border-indigo-200 bg-indigo-50 rounded-xl p-4">
                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={courtesyMode}
                        onChange={(e) => setCourtesyMode(e.target.checked)}
                        className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                      />
                      <div>
                        <span className="text-sm font-semibold text-indigo-900">Courtesy Scan</span>
                        <p className="text-xs text-indigo-600 mt-0.5">Send a full report to a partner with a sign-up pitch</p>
                      </div>
                    </label>
                    {courtesyMode && (
                      <div className="mt-4 space-y-3 pl-7">
                        <div>
                          <label htmlFor="courtesyName" className="block text-xs font-semibold text-indigo-800 mb-1">
                            Partner's name (optional)
                          </label>
                          <input
                            id="courtesyName"
                            type="text"
                            value={courtesyName}
                            onChange={(e) => setCourtesyName(e.target.value)}
                            placeholder="Jane Smith"
                            className="w-full px-3 py-2 rounded-lg border border-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-400 text-sm text-gray-900 placeholder-gray-400"
                          />
                        </div>
                        <div>
                          <label htmlFor="courtesyEmail" className="block text-xs font-semibold text-indigo-800 mb-1">
                            Send report to
                          </label>
                          <input
                            id="courtesyEmail"
                            type="email"
                            value={courtesyEmail}
                            onChange={(e) => setCourtesyEmail(e.target.value)}
                            placeholder="partner@theirbrokerage.com"
                            className="w-full px-3 py-2 rounded-lg border border-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-400 text-sm text-gray-900 placeholder-gray-400"
                            required={courtesyMode}
                          />
                          <p className="mt-1 text-xs text-indigo-500">They'll get the full report with fix instructions + a sign-up CTA</p>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* What's checked info */}
                <div className="bg-brand-blue-light rounded-lg p-4 text-sm text-gray-600">
                  <p className="font-semibold text-brand-blue mb-1">
                    {profession === 'realestate' ? '🏠 DRE Compliance Checks' : '📄 DRE + NMLS Compliance Checks'}
                  </p>
                  <p>{professionChecks}</p>
                </div>

                {/* Error display */}
                {error && (
                  <div className={`rounded-lg p-4 text-sm ${
                    error.type === 'limit_reached'
                      ? 'bg-amber-50 border border-amber-200 text-amber-900'
                      : 'bg-red-50 border border-red-200 text-red-900'
                  }`}>
                    <p className="font-semibold mb-1">
                      {ERROR_LABELS[error.type ?? ''] ?? '⚠️ Scan failed'}
                    </p>
                    <p>{error.message}</p>
                    {error.type === 'limit_reached' && (
                      <a href="/#pricing" className="inline-block mt-2 text-brand-gold font-semibold hover:underline">
                        Upgrade from $29.99/year →
                      </a>
                    )}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={loading || !url.trim() || !email.trim()}
                  className="w-full flex items-center justify-center gap-2 bg-brand-gold hover:bg-brand-gold-dark text-white font-bold py-4 rounded-xl text-lg shadow transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Start Scan
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M13 7l5 5m0 0l-5 5m5-5H6" />
                  </svg>
                </button>

                <p className="text-center text-xs text-gray-400">
                  First scan is always free. No credit card required.
                </p>
              </form>
            </div>

            {/* Trust strip */}
            <div className="text-center mt-6 text-xs text-gray-400 flex justify-center gap-6 flex-wrap">
              <span>🏛️ DRE Broker #01842442</span>
              <span>📋 NMLS #276626</span>
              <span>🔒 Results never shared</span>
            </div>
          </>
        ) : (
          /* Loading state */
          <div className="text-center py-16">
            <div className="w-20 h-20 mx-auto mb-8 relative">
              <svg className="w-20 h-20 animate-spin text-brand-gold" viewBox="0 0 100 100" fill="none">
                <circle cx="50" cy="50" r="40" stroke="#e5e7eb" strokeWidth="8" />
                <path
                  d="M 50 10 A 40 40 0 0 1 90 50"
                  stroke="#d97706"
                  strokeWidth="8"
                  strokeLinecap="round"
                />
              </svg>
              <div className="absolute inset-0 flex items-center justify-center">
                <svg className="w-8 h-8 text-brand-gold" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
              </div>
            </div>

            <h2 className="text-2xl font-bold text-gray-900 mb-2">Scanning your website…</h2>
            <p className="text-brand-gold font-medium mb-6 transition-all min-h-6">
              {PROGRESS_MESSAGES[progressIndex]}
            </p>

            <div className="max-w-sm mx-auto bg-white rounded-lg border border-gray-200 p-4 text-left">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Scanning</p>
              <p className="text-sm font-medium text-gray-700 truncate">{normalizeUrl(url)}</p>
              <p className="text-xs text-gray-400 mt-1">
                {profession === 'realestate' ? 'Real Estate Agent / Broker checks' : 'Mortgage Loan Officer checks'}
              </p>
            </div>

            <p className="text-xs text-gray-400 mt-6">This usually takes 15–30 seconds</p>
          </div>
        )}
      </div>
    </div>
  )
}
