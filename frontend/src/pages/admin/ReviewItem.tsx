import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Navbar from '../../components/Navbar'
import {
  getQueueItem, submitDecision, claimItem, releaseItem, uploadAsset, getAssetUrl,
  DECISIONS, REVIEWER_HINTS,
  type QueueDetailResponse, type ReviewAsset,
} from '../../lib/adminApi'

const STATUS_COLORS: Record<string, string> = {
  pass: 'text-green-600',
  fail: 'text-red-600',
  warn: 'text-amber-600',
  skip: 'text-gray-400',
}

const DECISION_COLORS: Record<string, string> = {
  confirmed_violation: 'bg-red-600 hover:bg-red-700 text-white',
  false_positive: 'bg-green-600 hover:bg-green-700 text-white',
  not_applicable: 'bg-gray-500 hover:bg-gray-600 text-white',
  needs_rescan: 'bg-blue-600 hover:bg-blue-700 text-white',
  scanner_bug: 'bg-purple-600 hover:bg-purple-700 text-white',
}

export default function ReviewItem() {
  const { itemId } = useParams<{ itemId: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<QueueDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedDecision, setSelectedDecision] = useState<string>('')
  const [note, setNote] = useState('')
  const [bugTag, setBugTag] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const loadItem = useCallback(async () => {
    if (!itemId) return
    setLoading(true)
    try {
      const result = await getQueueItem(itemId)
      setData(result)
      if (result.item.decision) setSelectedDecision(result.item.decision)
      if (result.item.reviewer_note) setNote(result.item.reviewer_note)
      if (result.item.bug_tag) setBugTag(result.item.bug_tag)
    } catch (e) {
      setError('Failed to load item')
    }
    setLoading(false)
  }, [itemId])

  useEffect(() => { loadItem() }, [loadItem])

  // Keyboard shortcuts
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      // 1-5 select decision
      const num = parseInt(e.key)
      if (num >= 1 && num <= 5) {
        setSelectedDecision(DECISIONS[num - 1].key)
        return
      }
      if (e.key === 'n') {
        e.preventDefault()
        document.getElementById('note-field')?.focus()
        return
      }
      if (e.key === 't') {
        e.preventDefault()
        document.getElementById('tag-field')?.focus()
        return
      }
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        handleSubmit()
        return
      }
      if (e.key === 'Escape') {
        navigate('/admin/queue')
        return
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  })

  async function handleSubmit() {
    if (!selectedDecision || !itemId || submitting) return
    setSubmitting(true)
    setError('')
    try {
      await submitDecision(itemId, selectedDecision, note, bugTag)
      navigate('/admin/queue')
    } catch (e: any) {
      setError(e.message || 'Failed to submit')
    }
    setSubmitting(false)
  }

  async function handleClaim() {
    if (!itemId) return
    try {
      await claimItem(itemId)
      loadItem()
    } catch (e: any) {
      setError(e.message)
    }
  }

  async function handleRelease() {
    if (!itemId) return
    try {
      await releaseItem(itemId)
      loadItem()
    } catch (e: any) {
      setError(e.message)
    }
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file || !itemId) return
    try {
      await uploadAsset(itemId, file)
      loadItem()
    } catch (err: any) {
      setError(err.message)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Navbar />
        <div className="flex items-center justify-center py-20">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600"></div>
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Navbar />
        <div className="max-w-4xl mx-auto px-4 py-10 text-center text-gray-500">
          Item not found. <button onClick={() => navigate('/admin/queue')} className="text-blue-600 underline">Back to queue</button>
        </div>
      </div>
    )
  }

  const { item, assets, scan_context } = data
  const hint = REVIEWER_HINTS[item.rule_id]
  const allChecks = scan_context?.checks || []
  const siteDisplay = item.site_url.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')

  return (
    <div className="min-h-screen bg-gray-50 pb-32">
      <Navbar />
      <div className="max-w-7xl mx-auto px-4 py-4">
        {/* Top bar */}
        <div className="flex items-center justify-between mb-4">
          <button onClick={() => navigate('/admin/queue')} className="text-sm text-gray-500 hover:text-blue-600">
            &larr; Back to Queue
          </button>
          <div className="flex gap-2">
            {item.review_status === 'pending' && (
              <button onClick={handleClaim} className="px-3 py-1.5 text-sm bg-blue-100 text-blue-700 rounded-lg hover:bg-blue-200">
                Claim
              </button>
            )}
            {item.review_status === 'claimed' && (
              <button onClick={handleRelease} className="px-3 py-1.5 text-sm bg-gray-100 text-gray-600 rounded-lg hover:bg-gray-200">
                Release
              </button>
            )}
          </div>
        </div>

        {/* Header */}
        <div className="bg-white rounded-xl border p-4 mb-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-lg font-bold text-gray-900">{siteDisplay}</h1>
              {item.page_url && item.page_url !== item.site_url && (
                <p className="text-xs text-gray-400 mt-0.5">Redirected to: {item.page_url}</p>
              )}
            </div>
            <div className="flex items-center gap-3 text-sm">
              <span className="text-gray-500">Score: <strong>{item.score ?? '-'}</strong></span>
              <span className="text-gray-500">{item.profession === 'lending' ? 'Lending' : 'RE'}</span>
              {item.entity_type !== 'standard' && (
                <span className="bg-purple-100 text-purple-700 px-2 py-0.5 rounded text-xs font-medium">
                  {item.entity_type}
                </span>
              )}
            </div>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span className={`font-semibold ${STATUS_COLORS[item.scanner_status]}`}>
              {item.scanner_status.toUpperCase()}
            </span>
            <span className="text-gray-400">|</span>
            <span className="font-medium text-gray-700">{item.rule_name}</span>
            <span className="text-xs text-gray-400">({item.rule_id})</span>
          </div>
        </div>

        {/* Reviewer hint */}
        {hint && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 mb-4 text-sm text-blue-800">
            <strong>Reviewer hint:</strong> {hint}
          </div>
        )}

        {/* Two-panel layout */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4 mb-4">
          {/* Left: Evidence */}
          <div className="lg:col-span-3 space-y-4">
            {/* Scanner detail */}
            <div className="bg-white rounded-xl border p-4">
              <h3 className="font-semibold text-gray-700 mb-2">Scanner Detail</h3>
              <p className="text-sm text-gray-600 whitespace-pre-wrap">{item.scanner_detail || 'No detail provided'}</p>
              {item.scanner_evidence && (
                <div className="mt-2 bg-gray-50 rounded p-2 text-xs text-gray-500 font-mono">
                  Evidence: {item.scanner_evidence}
                </div>
              )}
            </div>

            {/* All checks for this site */}
            {allChecks.length > 0 && (
              <div className="bg-white rounded-xl border p-4">
                <h3 className="font-semibold text-gray-700 mb-2">All Rules (this scan)</h3>
                <div className="space-y-1">
                  {allChecks.map((c: any) => (
                    <div key={c.id} className={`flex items-center gap-2 text-sm py-1 px-2 rounded ${
                      c.id === item.rule_id ? 'bg-yellow-50 font-semibold' : ''
                    }`}>
                      <span className={`w-12 text-xs font-bold ${STATUS_COLORS[c.status]}`}>
                        {c.status.toUpperCase()}
                      </span>
                      <span className="text-gray-700">{c.id}</span>
                      {c.id === item.rule_id && <span className="text-yellow-600 text-xs">&larr; reviewing</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Right: Assets + Links */}
          <div className="lg:col-span-2 space-y-4">
            {/* Quick links */}
            <div className="bg-white rounded-xl border p-4">
              <h3 className="font-semibold text-gray-700 mb-2">Quick Links</h3>
              <div className="space-y-2">
                <a href={item.page_url || item.site_url} target="_blank" rel="noopener"
                   className="block text-sm text-blue-600 hover:underline">
                  Open site in new tab &rarr;
                </a>
                {item.scan_id && (
                  <a href={`/results/${item.scan_id}`} target="_blank" rel="noopener"
                     className="block text-sm text-blue-600 hover:underline">
                    View full scan report &rarr;
                  </a>
                )}
              </div>
            </div>

            {/* Screenshots */}
            <div className="bg-white rounded-xl border p-4">
              <h3 className="font-semibold text-gray-700 mb-2">Screenshots</h3>
              {assets.length > 0 ? (
                <div className="space-y-2">
                  {assets.map((a: ReviewAsset) => (
                    <a key={a.id} href={getAssetUrl(a.storage_path)} target="_blank" rel="noopener">
                      <img
                        src={getAssetUrl(a.storage_path)}
                        alt={a.caption || 'Screenshot'}
                        className="w-full rounded border cursor-pointer hover:opacity-90"
                      />
                    </a>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-gray-400">No screenshots</p>
              )}
              <label className="mt-3 block">
                <span className="text-xs text-blue-600 hover:underline cursor-pointer">+ Upload screenshot</span>
                <input type="file" accept="image/*" onChange={handleUpload} className="hidden" />
              </label>
            </div>

            {/* Version info */}
            <div className="text-xs text-gray-400 space-y-0.5">
              <div>Scanner: {item.scanner_version || '?'} | Rule: {item.rule_version || '?'}</div>
              <div>Source: {item.source} | Created: {new Date(item.created_at).toLocaleString()}</div>
            </div>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-2 mb-4 text-sm text-red-700">
            {error}
          </div>
        )}
      </div>

      {/* Sticky decision bar */}
      <div className="fixed bottom-0 left-0 right-0 bg-white border-t shadow-lg px-4 py-3 z-50">
        <div className="max-w-7xl mx-auto flex items-center gap-3">
          {/* Decision buttons */}
          <div className="flex gap-2">
            {DECISIONS.map(d => (
              <button
                key={d.key}
                onClick={() => setSelectedDecision(d.key)}
                className={`px-3 py-2 rounded-lg text-sm font-medium transition-all ${
                  selectedDecision === d.key
                    ? DECISION_COLORS[d.key]
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                [{d.shortcut}] {d.label}
              </button>
            ))}
          </div>

          <div className="flex-1" />

          {/* Note + tag */}
          <input
            id="note-field"
            type="text"
            placeholder="Note (n)"
            value={note}
            onChange={e => setNote(e.target.value)}
            className="border rounded-lg px-3 py-2 text-sm w-48"
          />
          <input
            id="tag-field"
            type="text"
            placeholder="Bug tag (t)"
            value={bugTag}
            onChange={e => setBugTag(e.target.value)}
            className="border rounded-lg px-3 py-2 text-sm w-32"
          />

          {/* Submit */}
          <button
            onClick={handleSubmit}
            disabled={!selectedDecision || submitting}
            className="bg-brand-blue text-white px-5 py-2 rounded-lg text-sm font-semibold disabled:opacity-30 hover:opacity-90"
          >
            {submitting ? 'Saving...' : 'Submit (Ctrl+Enter)'}
          </button>
        </div>
        <div className="max-w-7xl mx-auto mt-1 text-xs text-gray-400">
          1-5 select decision | n=note | t=tag | Ctrl+Enter=submit | Esc=back
        </div>
      </div>
    </div>
  )
}
