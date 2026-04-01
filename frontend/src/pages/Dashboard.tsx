import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../App'
import Navbar from '../components/Navbar'
import { supabase } from '../lib/supabase'

interface ScanRecord {
  id: string
  url: string
  profession: string
  status: string
  score: number | null
  created_at: string
  plan?: string
}

export default function Dashboard() {
  const { user } = useAuth()
  const [scans, setScans] = useState<ScanRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [subscription, setSubscription] = useState<{
    plan: string
    scans_remaining: number | null
    expires_at: string | null
  } | null>(null)

  useEffect(() => {
    if (!user) return

    async function loadData() {
      // Fetch scan history
      const { data: scanData } = await supabase
        .from('scans')
        .select('id, url, profession, status, score, created_at, plan')
        .eq('user_id', user!.id)
        .order('created_at', { ascending: false })
        .limit(50)

      if (scanData) setScans(scanData)

      // Fetch subscription info
      const { data: subData } = await supabase
        .from('subscriptions')
        .select('plan, scans_remaining, current_period_end')
        .eq('user_id', user!.id)
        .eq('status', 'active')
        .maybeSingle()

      if (subData) {
        setSubscription({
          plan: subData.plan,
          scans_remaining: subData.scans_remaining,
          expires_at: subData.current_period_end,
        })
      }

      setLoading(false)
    }

    loadData()
  }, [user])

  function statusBadge(status: string) {
    switch (status) {
      case 'completed':
      case 'complete':
        return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">Complete</span>
      case 'failed':
      case 'error':
        return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">Failed</span>
      case 'running':
      case 'pending':
        return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">Running</span>
      default:
        return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">{status}</span>
    }
  }

  function scoreColor(score: number | null) {
    if (score === null) return 'text-gray-400'
    if (score >= 80) return 'text-green-600'
    if (score >= 60) return 'text-amber-600'
    return 'text-red-600'
  }

  function planLabel(plan: string) {
    const labels: Record<string, string> = {
      starter: 'Starter',
      professional: 'Professional',
      broker: 'Broker / Team',
      single: 'Single Scan',
    }
    return labels[plan] || plan
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />

      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8 sm:py-12">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
            <p className="text-gray-500 text-sm mt-1">Welcome back, {user?.email ?? 'user'}.</p>
          </div>
          <Link
            to="/scan"
            className="inline-flex items-center gap-2 bg-brand-gold hover:bg-brand-gold-dark text-white font-bold px-5 py-2.5 rounded-xl transition-colors shadow-sm text-sm"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 4v16m8-8H4" />
            </svg>
            New Scan
          </Link>
        </div>

        {/* Subscription card */}
        {subscription && (
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 mb-6">
            <div className="flex flex-wrap items-center gap-6">
              <div>
                <p className="text-xs text-gray-500 uppercase tracking-wide font-medium">Plan</p>
                <p className="text-lg font-bold text-brand-blue">{planLabel(subscription.plan)}</p>
              </div>
              {subscription.scans_remaining !== null && (
                <div>
                  <p className="text-xs text-gray-500 uppercase tracking-wide font-medium">Scans remaining</p>
                  <p className="text-lg font-bold text-gray-900">{subscription.scans_remaining}</p>
                </div>
              )}
              {subscription.expires_at && (
                <div>
                  <p className="text-xs text-gray-500 uppercase tracking-wide font-medium">Renews</p>
                  <p className="text-lg font-bold text-gray-900">
                    {new Date(subscription.expires_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                  </p>
                </div>
              )}
              <div className="sm:ml-auto">
                <Link to="/#pricing" className="text-sm text-brand-gold font-semibold hover:underline">
                  Manage plan
                </Link>
              </div>
            </div>
          </div>
        )}

        {/* Scan history */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-100">
            <h2 className="font-semibold text-gray-900">Scan History</h2>
          </div>

          {loading ? (
            <div className="p-12 text-center">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-brand-gold mx-auto"></div>
              <p className="text-sm text-gray-400 mt-3">Loading scans...</p>
            </div>
          ) : scans.length === 0 ? (
            <div className="p-12 text-center">
              <svg className="w-12 h-12 text-gray-300 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <p className="text-gray-500 font-medium">No scans yet</p>
              <p className="text-sm text-gray-400 mt-1">
                <Link to="/scan" className="text-brand-gold hover:underline">Run your first scan</Link> to see results here.
              </p>
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {scans.map((scan) => (
                <Link
                  key={scan.id}
                  to={`/results/${scan.id}`}
                  className="flex items-center gap-4 px-5 py-4 hover:bg-gray-50 transition-colors"
                >
                  {/* Score */}
                  <div className={`text-xl font-extrabold w-12 text-center ${scoreColor(scan.score)}`}>
                    {scan.score !== null ? scan.score : '--'}
                  </div>

                  {/* Details */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 truncate">{scan.url}</p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {scan.profession === 'lending' ? 'MLO' : 'RE Agent'} &middot;{' '}
                      {new Date(scan.created_at).toLocaleDateString('en-US', {
                        month: 'short',
                        day: 'numeric',
                        year: 'numeric',
                        hour: 'numeric',
                        minute: '2-digit',
                      })}
                    </p>
                  </div>

                  {/* Status */}
                  <div className="flex-shrink-0">
                    {statusBadge(scan.status)}
                  </div>

                  {/* Arrow */}
                  <svg className="w-4 h-4 text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Upgrade CTA for free users */}
        {!subscription && scans.length > 0 && (
          <div className="mt-6 bg-brand-blue-light rounded-xl border border-blue-100 p-5 text-center">
            <p className="text-gray-700 font-medium mb-1">Want ongoing compliance monitoring?</p>
            <p className="text-sm text-gray-500 mb-4">
              Upgrade to a paid plan for more scans, full fix reports, and webmaster email templates.
            </p>
            <Link
              to="/#pricing"
              className="inline-block bg-brand-gold hover:bg-brand-gold-dark text-white font-bold px-6 py-2.5 rounded-xl transition-colors text-sm"
            >
              View Plans
            </Link>
          </div>
        )}
      </div>
    </div>
  )
}
