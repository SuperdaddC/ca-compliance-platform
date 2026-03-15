import { useEffect, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import Navbar from '../components/Navbar'
import ScoreCircle from '../components/ScoreCircle'
import CheckResult from '../components/CheckResult'
import { useAuth } from '../App'
import { getScanResults, requestPdfReport } from '../lib/api'
import type { ScanResult } from '../lib/supabase'
import { PRICE_IDS, startCheckout } from '../lib/stripe'

type ScanStatus = 'loading' | 'running' | 'complete' | 'error'

interface ScanData {
  id: string
  url: string
  profession: string
  status: string
  score: number
  results: ScanResult[]
  created_at: string
  tier?: 'free' | 'single' | 'pro' | 'broker'
  profession_override?: string
}

function isPaid(tier: string | undefined): boolean {
  return tier === 'single' || tier === 'pro' || tier === 'broker'
}

export default function Results() {
  const { scanId } = useParams<{ scanId: string }>()
  const { user } = useAuth()
  const navigate = useNavigate()

  const [status, setStatus] = useState<ScanStatus>('loading')
  const [scan, setScan] = useState<ScanData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [downloadingPdf, setDownloadingPdf] = useState(false)
  const [pollCount, setPollCount] = useState(0)

  useEffect(() => {
    if (!scanId) {
      navigate('/')
      return
    }
    fetchResults()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanId])

  useEffect(() => {
    // Poll if scan is still running
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

  async function fetchResults() {
    if (!scanId) return
    try {
      const data = await getScanResults(scanId) as ScanData
      if (data.status === 'complete') {
        setScan(data)
        setStatus('complete')
      } else if (data.status === 'error') {
        setError('Scan encountered an error. Please try again.')
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

  async function handleDownloadPdf() {
    if (!scanId) return
    setDownloadingPdf(true)
    try {
      const { url } = await requestPdfReport(scanId)
      window.open(url, '_blank')
    } catch (err) {
      alert(err instanceof Error ? err.message : 'PDF generation failed. Please try again.')
    } finally {
      setDownloadingPdf(false)
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
          <h2 className="text-xl font-bold text-gray-900 mb-2">Something went wrong</h2>
          <p className="text-gray-500 mb-6 max-w-sm">{error ?? 'Unable to load scan results.'}</p>
          <Link to="/scan" className="btn-primary">Try Another Scan</Link>
        </div>
      </div>
    )
  }

  const results = scan.results ?? []
  const passed = results.filter((r) => r.status === 'pass').length
  const warnings = results.filter((r) => r.status === 'warn').length
  const failed = results.filter((r) => r.status === 'fail').length
  const userIsPaid = isPaid(scan.tier)
  const isOnFreeTier = !userIsPaid

  // Group by category
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
            {scan.profession === 'mortgage' ? 'Mortgage Loan Officer' : 'Real Estate Agent/Broker'} •{' '}
            {new Date(scan.created_at).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
          </p>
        </div>

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
                    onClick={() => startCheckout(PRICE_IDS.SINGLE_SCAN, scanId)}
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
                onClick={() => startCheckout(PRICE_IDS.SINGLE_SCAN, scanId)}
                className="flex-shrink-0 bg-brand-gold hover:bg-brand-gold-dark text-white font-bold px-6 py-3 rounded-xl transition-colors shadow"
              >
                Get Fix Instructions — $19
              </button>
            </div>
          </div>
        )}

        {/* Profession Override Notice */}
        {scan.profession_override && (
          <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 mb-4 flex gap-3">
            <svg className="w-5 h-5 text-blue-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <p className="text-sm text-blue-800">
              <span className="font-semibold">Profession auto-detected:</span> {scan.profession_override}
            </p>
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
              Pro gives you unlimited scans as you make updates.
            </p>
            <div className="flex flex-col sm:flex-row gap-3 justify-center">
              <button
                onClick={() => startCheckout(PRICE_IDS.SINGLE_SCAN, scanId)}
                className="bg-brand-gold hover:bg-brand-gold-dark text-white font-bold px-6 py-3 rounded-xl transition-colors"
              >
                Get This Report — $19
              </button>
              <button
                onClick={() => startCheckout(PRICE_IDS.PRO_MONTHLY)}
                className="bg-brand-blue hover:bg-blue-900 text-white font-bold px-6 py-3 rounded-xl transition-colors"
              >
                Go Pro — $39/mo
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
