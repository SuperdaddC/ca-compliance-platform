import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import Navbar from '../components/Navbar'

export default function CheckoutSuccess() {
  const [searchParams] = useSearchParams()
  const [countdown, setCountdown] = useState(5)
  const scanId = searchParams.get('scan_id')

  useEffect(() => {
    const timer = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          clearInterval(timer)
          // Redirect to scan results or dashboard
          if (scanId) {
            window.location.href = `/results/${scanId}?payment=success`
          } else {
            window.location.href = '/dashboard'
          }
          return 0
        }
        return c - 1
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [scanId])

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />

      <div className="max-w-lg mx-auto px-4 py-20 text-center">
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-8">
          <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-5">
            <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
            </svg>
          </div>

          <h1 className="text-2xl font-bold text-gray-900 mb-2">Payment Confirmed!</h1>
          <p className="text-gray-500 mb-6">
            Thank you for your purchase. Your full compliance report is now unlocked.
          </p>

          <div className="flex flex-col gap-3">
            {scanId ? (
              <Link
                to={`/results/${scanId}?payment=success`}
                className="bg-brand-gold hover:bg-brand-gold-dark text-white font-bold px-6 py-3 rounded-xl transition-colors"
              >
                View Your Report
              </Link>
            ) : (
              <Link
                to="/dashboard"
                className="bg-brand-gold hover:bg-brand-gold-dark text-white font-bold px-6 py-3 rounded-xl transition-colors"
              >
                Go to Dashboard
              </Link>
            )}
            <Link
              to="/scan"
              className="text-sm text-brand-blue font-medium hover:text-brand-gold transition-colors"
            >
              Run Another Scan
            </Link>
          </div>

          <p className="text-xs text-gray-400 mt-6">
            Redirecting in {countdown} seconds...
          </p>
        </div>
      </div>
    </div>
  )
}
