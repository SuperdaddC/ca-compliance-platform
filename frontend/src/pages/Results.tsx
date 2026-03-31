import { useEffect, useState, useRef } from 'react'
import { useParams, Link, useNavigate, useSearchParams, useLocation } from 'react-router-dom'
import Navbar from '../components/Navbar'
import ScoreCircle from '../components/ScoreCircle'
import CheckResult from '../components/CheckResult'
import { useAuth } from '../App'
import { getScanResult, retryScan, requestPdfReport, createCheckout } from '../lib/api'
import type { ScanResult as ScanResultType } from '../lib/api'

type ScanStatus = 'loading' | 'running' | 'complete' | 'error'

interface ScanData {
  id: string
  url: string
  profession: string
  status: string
  score: number
  results: {
    id: string
    label: string
    category: string
    status: 'pass' | 'warn' | 'fail' | 'na'
    detail: string
    remediation?: string
    screenshot_url?: string
  }[]
  created_at: string
  plan?: string
  error_type?: string
  error_message?: string
}

function isPaid(plan: string | undefined): boolean {
  return plan === 'starter' || plan === 'professional' || plan === 'broker' || plan === 'single'
}

function transformResult(raw: ScanResultType): ScanData {
  return {
    id: raw.scan_id,
    url: raw.url,
    profession: raw.profession,
    status: raw.status === 'completed' ? 'complete' : raw.status === 'failed' ? 'error' : raw.status,
    score: raw.score,
    results: (raw.checks ?? []).map((c) => ({
      id: c.id,
      label: c.name,
      category: 'General',
      status: c.status === 'skip' ? 'na' as const : c.status,
      detail: c.description,
      remediation: c.fix ?? undefined,
    })),
    created_at: new Date().toISOString(),
    plan: raw.plan ?? (raw.is_free_scan ? undefined : 'single'),
    error_type: raw.error_type,
    error_message: raw.error_message,
  }
}

const ERROR_LABELS: Record<string, string> = {
  timeout: '⏱ The website took too long to respond',
  blocked: '🚧 The website blocked the scan',
  dns_fail: '🔍 Domain not found',
  ssl_error: '🔒 SSL certificate issue',
  empty_page: '📄 Page rendered empty',
  rate_limited: '⏳ Temporarily rate-limited',
}

export default function Results() {
  const { scanId } = useParams<{ scanId: string }>()
  const [searchParams] = useSearchParams()
  const location = useLocation()
  const { user } = useAuth()
  const navigate = useNavigate()

  const [status, setStatus] = useState<ScanStatus>('loading')
  const [scan, setScan] = useState<ScanData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [errorType, setErrorType] = useState<string | undefined>()
  const [downloadingPdf, setDownloadingPdf] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [pollCount, setPollCount] = useState(0)
  const [paymentSuccess, setPaymentSuccess] = useState(false)
  const paymentPollRef = useRef(0)

  useEffect(() => {
    if (!scanId) {
      navigate('/')
      return
    }
    if (searchParams.get('payment') === 'success') {
      setPaymentSuccess(true)
    }

    // If navigated from Scan page with result in state, use it directly
    const navState = location.state as { result?: ScanResultType } | null
    if (navState?.result) {
      const data = transformResult(navState.result)
      setScan(data)
      setStatus(data.status === 'complete' ? 'complete' : data.status === 'error' ? 'error' : 'running')
      if (data.status === 'error') {
        setError(data.error_message ?? 'Scan failed.')
        setErrorType(data.error_type)
      }
    } else {
      fetchResults()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanId])

  useEffect(() => {
    if (status === 'running' && pollCount < 30) {
      const timer = setTimeout(() => {
        setPollCount((c) => c + 1)
        fetchResults()
      }, 3000)
      return () => clearTimeout(timer)
    }
    if (status === 'running' && pollCount >= 30) {
      setError('Scan is taking longer than expected. Please refresh to check again.')
      setStatus('error')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, pollCount])

  useEffect(() => {
    if (!paymentSuccess || !scan) return
    if (isPaid(scan.plan)) return
    if (paymentPollRef.current >= 10) return

    const timer = setTimeout(async () => {
      paymentPollRef.current += 1
      await fetchResults()
    }, 2000)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paymentSuccess, scan])

  async function fetchResults() {
    if (!scanId) return
    try {
      const raw = await getScanResult(scanId)
      const data = transformResult(raw)
      if (data.status === 'complete') {
        setScan(data)
        setStatus('complete')
      } else if (data.status === 'error') {
        setScan(data)
        setError(data.error_message ?? 'Scan encountered an error.')
        setErrorType(data.error_type)
        setStatus('error')
      } else {
        setScan(data)
        setStatus('running')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load results.')
      setStatus('error')
    }
  }

  async function handleRetry() {
    if (!scanId) return
    setRetrying(true)
    setError(null)
    setErrorType(undefined)
    try {
      const raw = await retryScan(scanId)
      const data = transformResult(raw)
      setScan(data)
      setStatus(data.status === 'complete' ? 'complete' : data.status === 'error' ? 'error' : 'running')
      setPollCount(0)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Retry failed. Please try again.')
      setStatus('error')
    } finally {
      setRetrying(false)
    }
  }

  async function handleDownloadPdf() {
    if (!scanId) return
    setDownloadingPdf(true)
    try {
      await requestPdfReport(scanId)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'PDF generation failed. Please try again.')
    } finally {
      setDownloadingPdf(false)
    }
  }

  async function handleCheckout(plan: 'single' | 'starter' | 'professional' | 'broker') {
    try {
      const url = await createCheckout({
        plan,
        email: user?.email,
        userId: user?.id,
        scanId,
      })
      window.location.href = url
    } catch {
      alert('Could not start checkout. Please try again.')
    }
  }

  function handleScreenshotUploaded(checkId: string, screenshotUrl: string) {
    setScan((prev) => {
      if (!prev) return prev
      return {
        ...prev,
        results: prev.results.map((r) =>
          r.id === checkId ? { ...r, screenshot_url: screenshotUrl } : r
        ),
      }
    })
  }

  // Loading / polling state
  if (status === 'loading' || status === 'running') {
    return (
      <div className="min-h-screen bg-gray-50">
        <Navbar />
        <div className="flex flex-col items-center justify-center py-24 px-4">
          <div className="animate-spin rounded-full h-14 w-14 border-4 border-gray-200 border-t-brand-gold mb-6"></div>
          <h2 className="text-xl font-bold text-gray-900 mb-2">
            {status === 'loading' ? 'Loading your results…' : 'Scan in progress…'}
          </h2>
          {scan?.url && (
            <p className="text-gray-500 text-sm">{scan.url}</p>
          )}
          {status === 'running' && (
            <p className="text-xs text-gray-400 mt-4">
              This usually takes 15–30 seconds. Checking back automatically…
            </p>
          )}
        </div>
      </div>
    )
  }

  // Error state with retry
  if (status === 'error' || !scan) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Navbar />
        <div className="flex flex-col items-center justify-center py-24 px-4 text-center">
          <div className="w-14 h-14 bg-red-100 rounded-full flex items-center justify-center mb-5">
            <svg className="w-7 h-7 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
          <h2 className="text-xl font-bold text-gray-900 mb-2">
            {errorType ? (ERROR_LABELS[errorType] ?? 'Scan failed') : 'Something went wrong'}
          </h2>
          <p className="text-gray-500 mb-6 max-w-sm">{error ?? 'Unable to load scan results.'}</p>
          <div className="flex gap-3">
            {scanId && (
              <button
                onClick={handleRetry}
                disabled={retrying}
                className="flex items-center gap-2 bg-brand-gold hover:bg-brand-gold-dark text-white font-semibold px-5 py-2.5 rounded-lg transition-colors disabled:opacity-50"
              >
                {retrying ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    Retrying…
                  </>
                ) : (
                  'Retry Scan'
                )}
              </button>
            )}
            <Link to="/scan" className="border border-gray-300 text-gray-700 font-semibold px-5 py-2.5 rounded-lg hover:bg-gray-50 transition-colors">
              Try Another URL
            </Link>
          </div>
        </div>
      </div>
    )
  }

  const results = (scan.results ?? []).filter((r) => r.status !== 'na')
  const passed = results.filter((r) => r.status === 'pass').length
  const warnings = results.filter((r) => r.status === 'warn').length
  const failed = results.filter((r) => r.status === 'fail').length
  const userIsPaid = isPaid(scan.plan)
  const isOnFreeTier = !userIsPaid

  const categories = Array.from(new Set(results.map((r) => r.category))).sort()

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />

      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8 sm:py-12">

        {/* Header */}
        <div className="mb-6">
          <div className="flex items-center gap-2 text-sm text-gray-500 mb-1">
            <Link to="/" className="hover:text-brand-blue transition-colors">Home</Link>
            <span>/</span>
            <span className="text-gray-900 font-medium truncate max-w-xs">{scan.url}</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-bold text-gray-900">
            Compliance Report
          </h1>
          <p className="text-gray-500 text-sm mt-1">
            {scan.profession === 'lending' ? 'Mortgage Loan Officer' : 'Real Estate Agent/Broker'} •{' '}
            {new Date(scan.created_at).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
          </p>
        </div>

        {/* Payment success banner */}
        {paymentSuccess && (
          <div className={`rounded-xl p-4 mb-6 flex items-center gap-3 ${isPaid(scan.plan) ? 'bg-green-50 border border-green-200' : 'bg-blue-50 border border-blue-200'}`}>
            {isPaid(scan.plan) ? (
              <>
                <svg className="w-5 h-5 text-green-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                </svg>
                <p className="text-sm font-medium text-green-800">Payment confirmed — your full report is now unlocked.</p>
              </>
            ) : (
              <>
                <div className="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin flex-shrink-0" />
                <p className="text-sm font-medium text-blue-800">Payment received — unlocking your report…</p>
              </>
            )}
          </div>
        )}

        {/* Score + summary */}
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6 sm:p-8 mb-6">
          <div className="flex flex-col sm:flex-row items-center gap-8">
            <ScoreCircle score={scan.score} size={160} />

            <div className="flex-1 w-full">
              <h2 className="text-lg font-bold text-gray-900 mb-4 text-center sm:text-left">
                {scan.url}
              </h2>

              <div className="grid grid-cols-3 gap-3 mb-6">
                <div className="text-center bg-green-50 rounded-xl p-3 border border-green-100">
                  <p className="text-2xl font-bold text-green-600">{passed}</p>
                  <p className="text-xs font-medium text-green-700 mt-0.5">Passed</p>
                </div>
                <div className="text-center bg-amber-50 rounded-xl p-3 border border-amber-100">
                  <p className="text-2xl font-bold text-amber-600">{warnings}</p>
                  <p className="text-xs font-medium text-amber-700 mt-0.5">Warnings</p>
                </div>
                <div className="text-center bg-red-50 rounded-xl p-3 border border-red-100">
                  <p className="text-2xl font-bold text-red-600">{failed}</p>
                  <p className="text-xs font-medium text-red-700 mt-0.5">Failed</p>
                </div>
              </div>

              {/* Actions */}
              <div className="flex flex-wrap gap-3">
                {userIsPaid ? (
                  <button
                    onClick={handleDownloadPdf}
                    disabled={downloadingPdf}
                    className="flex items-center gap-2 bg-brand-blue text-white text-sm font-semibold px-4 py-2 rounded-lg hover:bg-blue-900 transition-colors disabled:opacity-60"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                    {downloadingPdf ? 'Generating PDF…' : 'Download PDF Report'}
                  </button>
                ) : (
                  <button
                    onClick={() => handleCheckout('single')}
                    className="flex items-center gap-2 bg-gray-100 text-gray-500 text-sm font-semibold px-4 py-2 rounded-lg hover:bg-gray-200 transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                    </svg>
                    Download PDF (upgrade required)
                  </button>
                )}

                <Link
                  to="/scan"
                  className="flex items-center gap-2 border border-gray-300 text-gray-700 text-sm font-semibold px-4 py-2 rounded-lg hover:bg-gray-50 transition-colors"
                >
                  Scan another URL
                </Link>
              </div>
            </div>
          </div>
        </div>

        {/* Upgrade CTA (free tier) */}
        {isOnFreeTier && failed > 0 && (
          <div className="bg-gradient-to-r from-brand-blue to-blue-800 rounded-2xl p-6 mb-6 text-white">
            <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
              <div className="flex-1">
                <h3 className="font-bold text-lg mb-1">
                  {failed} violation{failed !== 1 ? 's' : ''} need fixing
                </h3>
                <p className="text-blue-200 text-sm">
                  Upgrade to see exactly what to change on your website — no guesswork,
                  no Googling DRE guidelines. Just clear fix instructions.
                </p>
              </div>
              <button
                onClick={() => handleCheckout('single')}
                className="flex-shrink-0 bg-brand-gold hover:bg-brand-gold-dark text-white font-bold px-6 py-3 rounded-xl transition-colors shadow"
              >
                Get Fix Instructions — $19
              </button>
            </div>
          </div>
        )}

        {/* Legal Disclaimer Banner */}
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 mb-6 flex gap-3">
          <svg className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
          </svg>
          <p className="text-sm text-amber-800">
            <span className="font-semibold">Important:</span> This tool checks for common compliance indicators only.
            A passing score is <span className="font-semibold">not a legal determination of compliance.</span> Regulations
            have proximity, prominence, and content requirements that automated text scanning cannot fully verify.
            Consult a qualified compliance attorney before relying on these results.
          </p>
        </div>

        {/* Results list */}
        <div className="space-y-6">
          {categories.map((category) => {
            const categoryResults = results.filter((r) => r.category === category)
            return (
              <div key={category}>
                <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
                  {category}
                </h3>
                <div className="space-y-2">
                  {categoryResults.map((check) => (
                    <CheckResult
                      key={check.id}
                      scanId={scan.id}
                      check={check}
                      isPaidTier={userIsPaid}
                      onScreenshotUploaded={handleScreenshotUploaded}
                    />
                  ))}
                </div>
              </div>
            )
          })}
        </div>

        {/* Bottom upgrade section */}
        {isOnFreeTier && (
          <div className="mt-10 bg-white rounded-2xl border border-gray-200 shadow-sm p-6 text-center">
            <p className="text-gray-600 font-medium mb-1">Want to fix this permanently?</p>
            <p className="text-sm text-gray-500 mb-5">
              Single scan gives you the full fix guide for this report.
              Starter gives you 5 scans per year as you make updates.
            </p>
            <div className="flex flex-col sm:flex-row gap-3 justify-center">
              <button
                onClick={() => handleCheckout('single')}
                className="bg-brand-gold hover:bg-brand-gold-dark text-white font-bold px-6 py-3 rounded-xl transition-colors"
              >
                Get This Report — $19
              </button>
              <button
                onClick={() => handleCheckout('starter')}
                className="bg-brand-blue hover:bg-blue-900 text-white font-bold px-6 py-3 rounded-xl transition-colors"
              >
                Go Starter — $29/year
              </button>
            </div>
            {!user && (
              <p className="text-xs text-gray-400 mt-4">
                Already have an account?{' '}
                <Link to="/login" className="text-brand-blue font-medium hover:text-brand-gold transition-colors">
                  Log in to see your saved scans
                </Link>
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
