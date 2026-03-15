import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Navbar from '../components/Navbar'
import { startScan } from '../lib/api'

type Profession = 'real_estate' | 'mortgage'

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

export default function Scan() {
  const navigate = useNavigate()
  const [url, setUrl] = useState('')
  const [profession, setProfession] = useState<Profession>('real_estate')
  const [email, setEmail] = useState('')
  const [emailResults, setEmailResults] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [progressIndex, setProgressIndex] = useState(0)

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
      setError(urlError)
      return
    }

    setLoading(true)
    setError(null)

    // Start progress cycling
    let idx = 0
    const interval = setInterval(() => {
      idx = (idx + 1) % PROGRESS_MESSAGES.length
      setProgressIndex(idx)
    }, 2000)

    try {
      const result = await startScan({
        url: normalizeUrl(url),
        profession,
        ...(emailResults && email.trim() ? { email: email.trim() } : {}),
      })
      clearInterval(interval)
      if (result.error || result.status === 'failed') {
        throw new Error(result.error || 'Scan failed. The site may be blocking automated access.')
      }
      navigate(`/results/${result.scan_id}`)
    } catch (err) {
      clearInterval(interval)
      setError(err instanceof Error ? err.message : 'Scan failed. Please try again.')
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />

      <div className="max-w-2xl mx-auto px-4 sm:px-6 py-12 sm:py-16">
        {!loading ? (
          <>
            <div className="text-center mb-10">
              <h1 className="text-3xl font-bold text-gray-900 mb-2">
                Scan your website
              </h1>
              <p className="text-gray-500">
                Enter your website URL and we'll check it against California DRE and NMLS requirements.
              </p>
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
                      disabled={loading}
                      autoFocus
                    />
                  </div>
                  {error && (
                    <p className="mt-2 text-sm text-red-600">{error}</p>
                  )}
                </div>

                {/* Profession Selector */}
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">
                    Your profession
                  </label>
                  <div className="grid grid-cols-2 gap-3">
                    {([
                      { value: 'real_estate', label: 'Real Estate Agent / Broker', icon: '🏠' },
                      { value: 'mortgage', label: 'Mortgage Loan Officer', icon: '📄' },
                    ] as { value: Profession; label: string; icon: string }[]).map((opt) => (
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

                {/* What's checked info */}
                <div className="bg-brand-blue-light rounded-lg p-4 text-sm text-gray-600">
                  <p className="font-semibold text-brand-blue mb-1">
                    {profession === 'real_estate' ? '🏠 DRE Compliance Checks' : '📄 DRE + NMLS Compliance Checks'}
                  </p>
                  <p>
                    {profession === 'real_estate'
                      ? 'We check your DRE license display, Equal Housing compliance, team advertising rules, disclosure language, and more.'
                      : 'We check DRE and NMLS license display, Equal Housing / Equal Opportunity lender logos, advertising regulations, and required disclosures.'
                    }
                  </p>
                </div>

                {/* Email capture */}
                <div>
                  <label className="flex items-center gap-3 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={emailResults}
                      onChange={(e) => setEmailResults(e.target.checked)}
                      className="w-4 h-4 rounded border-gray-300 text-brand-gold focus:ring-brand-gold cursor-pointer"
                    />
                    <span className="text-sm font-medium text-gray-700">Email me my results</span>
                  </label>
                  {emailResults && (
                    <div className="mt-3">
                      <input
                        type="email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        placeholder="your@email.com"
                        className="w-full px-4 py-2.5 rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-brand-gold focus:border-transparent text-gray-900 placeholder-gray-400 text-sm"
                        required={emailResults}
                        autoComplete="email"
                      />
                      <p className="mt-1.5 text-xs text-gray-400">Free scan: score summary. Paid report: full PDF with fix instructions.</p>
                    </div>
                  )}
                </div>

                <button
                  type="submit"
                  disabled={loading || !url.trim() || (emailResults && !email.trim())}
                  className="w-full flex items-center justify-center gap-2 bg-brand-gold hover:bg-brand-gold-dark text-white font-bold py-4 rounded-xl text-lg shadow transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Start Scan
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M13 7l5 5m0 0l-5 5m5-5H6" />
                  </svg>
                </button>

                <p className="text-center text-xs text-gray-400">
                  First scan is always free. No account required.
                </p>
              </form>
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
                {profession === 'real_estate' ? 'Real Estate Agent / Broker checks' : 'Mortgage Loan Officer checks'}
              </p>
            </div>

            <p className="text-xs text-gray-400 mt-6">This usually takes 15–30 seconds</p>
          </div>
        )}
      </div>
    </div>
  )
}
