import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getScanResult } from '../lib/api'

interface BadgeData {
  score: number
  url: string
  date: string
}

export default function Badge() {
  const { scanId } = useParams<{ scanId: string }>()
  const [badge, setBadge] = useState<BadgeData | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (!scanId) return
    getScanResult(scanId)
      .then((r) => {
        setBadge({
          score: r.score,
          url: r.url,
          date: new Date().toLocaleDateString('en-US', { month: 'short', year: 'numeric' }),
        })
      })
      .catch(() => setError(true))
  }, [scanId])

  if (error) return null
  if (!badge) return null

  const color = badge.score >= 80 ? '#16a34a' : badge.score >= 60 ? '#d97706' : '#dc2626'
  const label = badge.score >= 80 ? 'Good Standing' : badge.score >= 60 ? 'Needs Review' : 'Action Required'

  return (
    <a
      href={`https://complywithjudy.com/results/${scanId}`}
      target="_blank"
      rel="noopener noreferrer"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '10px',
        padding: '10px 16px',
        background: '#ffffff',
        border: '1px solid #e5e7eb',
        borderRadius: '12px',
        textDecoration: 'none',
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
        boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
        maxWidth: '280px',
      }}
    >
      {/* Shield icon */}
      <div style={{
        width: '36px',
        height: '36px',
        borderRadius: '8px',
        background: color,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          {badge.score >= 60 && <path d="M9 12l2 2 4-4" />}
        </svg>
      </div>

      <div style={{ minWidth: 0 }}>
        <div style={{
          fontSize: '12px',
          fontWeight: 700,
          color: '#1a2744',
          lineHeight: 1.3,
        }}>
          ComplyWithJudy
        </div>
        <div style={{
          fontSize: '11px',
          fontWeight: 600,
          color: color,
          lineHeight: 1.3,
        }}>
          {badge.score}/100 — {label}
        </div>
        <div style={{
          fontSize: '10px',
          color: '#9ca3af',
          lineHeight: 1.3,
        }}>
          Verified {badge.date}
        </div>
      </div>
    </a>
  )
}
