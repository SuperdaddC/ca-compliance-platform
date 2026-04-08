import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Navbar from '../../components/Navbar'
import { getReviewQueue, getQueueStats, DECISIONS, type ReviewItem, type QueueStats, type QueueFilters } from '../../lib/adminApi'

const STATUS_COLORS: Record<string, string> = {
  fail: 'bg-red-50',
  warn: 'bg-amber-50',
  pass: 'bg-green-50',
  skip: 'bg-gray-50',
}

const DECISION_BADGES: Record<string, { label: string; color: string }> = {
  confirmed_violation: { label: 'Confirmed', color: 'bg-red-100 text-red-700' },
  false_positive: { label: 'False Pos', color: 'bg-green-100 text-green-700' },
  not_applicable: { label: 'N/A', color: 'bg-gray-100 text-gray-600' },
  needs_rescan: { label: 'Rescan', color: 'bg-blue-100 text-blue-700' },
  scanner_bug: { label: 'Bug', color: 'bg-purple-100 text-purple-700' },
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h`
  const days = Math.floor(hrs / 24)
  return `${days}d`
}

export default function ReviewQueue() {
  const navigate = useNavigate()
  const [items, setItems] = useState<ReviewItem[]>([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<QueueStats[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [filters, setFilters] = useState<QueueFilters>({
    review_status: 'pending',
    page: 0,
    per_page: 50,
  })

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [queueData, statsData] = await Promise.all([
        getReviewQueue(filters),
        getQueueStats(),
      ])
      setItems(queueData.items)
      setTotal(queueData.total)
      setStats(statsData)
    } catch (e) {
      console.error('Failed to load queue:', e)
    }
    setLoading(false)
  }, [filters])

  useEffect(() => { loadData() }, [loadData])

  // Keyboard navigation
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return
      if (e.key === 'j') setSelectedIdx(i => Math.min(i + 1, items.length - 1))
      if (e.key === 'k') setSelectedIdx(i => Math.max(i - 1, 0))
      if (e.key === 'Enter' && items[selectedIdx]) navigate(`/admin/queue/${items[selectedIdx].id}`)
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [items, selectedIdx, navigate])

  const pendingCount = stats.find(s => s.review_status === 'pending')?.total ?? 0
  const claimedCount = stats.find(s => s.review_status === 'claimed')?.total ?? 0
  const completedStats = stats.find(s => s.review_status === 'completed')
  const bugsCount = completedStats?.bugs_found ?? 0

  const uniqueRules = [...new Set(items.map(i => i.rule_id))].sort()

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />
      <div className="max-w-7xl mx-auto px-4 py-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-2xl font-bold text-gray-900">Review Queue</h1>
          <div className="flex gap-4 text-sm">
            <span className="bg-yellow-100 text-yellow-800 px-3 py-1 rounded-full font-medium">
              Pending: {pendingCount}
            </span>
            <span className="bg-blue-100 text-blue-800 px-3 py-1 rounded-full font-medium">
              Claimed: {claimedCount}
            </span>
            <span className="bg-purple-100 text-purple-800 px-3 py-1 rounded-full font-medium">
              Bugs: {bugsCount}
            </span>
          </div>
        </div>

        {/* Filters */}
        <div className="flex gap-3 mb-4">
          <select
            value={filters.review_status || ''}
            onChange={e => setFilters(f => ({ ...f, review_status: e.target.value || undefined, page: 0 }))}
            className="border rounded-lg px-3 py-2 text-sm bg-white"
          >
            <option value="pending">Pending</option>
            <option value="claimed">Claimed</option>
            <option value="completed">Completed</option>
            <option value="skipped">Skipped</option>
            <option value="">All</option>
          </select>
          <select
            value={filters.rule_id || ''}
            onChange={e => setFilters(f => ({ ...f, rule_id: e.target.value || undefined, page: 0 }))}
            className="border rounded-lg px-3 py-2 text-sm bg-white"
          >
            <option value="">All Rules</option>
            {uniqueRules.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
          <select
            value={filters.profession || ''}
            onChange={e => setFilters(f => ({ ...f, profession: e.target.value || undefined, page: 0 }))}
            className="border rounded-lg px-3 py-2 text-sm bg-white"
          >
            <option value="">All Professions</option>
            <option value="realestate">Real Estate</option>
            <option value="lending">Lending</option>
          </select>
        </div>

        {/* Table */}
        {loading ? (
          <div className="text-center py-12 text-gray-400">Loading...</div>
        ) : items.length === 0 ? (
          <div className="text-center py-12 text-gray-400">No items in queue</div>
        ) : (
          <div className="bg-white rounded-xl shadow-sm border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">Website</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">Rule</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">Status</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">Score</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">Age</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">Decision</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item, idx) => (
                  <tr
                    key={item.id}
                    onClick={() => navigate(`/admin/queue/${item.id}`)}
                    className={`border-b cursor-pointer transition-colors ${
                      idx === selectedIdx ? 'ring-2 ring-inset ring-blue-400' : ''
                    } ${STATUS_COLORS[item.scanner_status] || ''} hover:bg-blue-50`}
                  >
                    <td className="px-4 py-3 font-medium text-gray-900 truncate max-w-[200px]">
                      {item.site_url.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')}
                    </td>
                    <td className="px-4 py-3 text-gray-600">{item.rule_id}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${
                        item.scanner_status === 'fail' ? 'bg-red-100 text-red-700' :
                        item.scanner_status === 'warn' ? 'bg-amber-100 text-amber-700' :
                        'bg-gray-100 text-gray-600'
                      }`}>
                        {item.scanner_status.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-600">{item.score ?? '-'}</td>
                    <td className="px-4 py-3 text-gray-400">{timeAgo(item.created_at)}</td>
                    <td className="px-4 py-3">
                      {item.decision ? (
                        <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${
                          DECISION_BADGES[item.decision]?.color || 'bg-gray-100'
                        }`}>
                          {DECISION_BADGES[item.decision]?.label || item.decision}
                        </span>
                      ) : item.claimed_by ? (
                        <span className="text-xs text-blue-600 font-medium">Claimed</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {total > (filters.per_page ?? 50) && (
          <div className="flex items-center justify-between mt-4">
            <button
              disabled={(filters.page ?? 0) === 0}
              onClick={() => setFilters(f => ({ ...f, page: (f.page ?? 0) - 1 }))}
              className="px-4 py-2 text-sm border rounded-lg disabled:opacity-30"
            >
              Prev
            </button>
            <span className="text-sm text-gray-500">
              Page {(filters.page ?? 0) + 1} of {Math.ceil(total / (filters.per_page ?? 50))}
            </span>
            <button
              disabled={((filters.page ?? 0) + 1) * (filters.per_page ?? 50) >= total}
              onClick={() => setFilters(f => ({ ...f, page: (f.page ?? 0) + 1 }))}
              className="px-4 py-2 text-sm border rounded-lg disabled:opacity-30"
            >
              Next
            </button>
          </div>
        )}

        <div className="mt-4 text-xs text-gray-400">
          Keyboard: j/k navigate, Enter open item
        </div>
      </div>
    </div>
  )
}
