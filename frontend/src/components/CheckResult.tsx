import { useState } from 'react'
import { uploadScreenshot } from '../lib/api'

interface CheckResultProps {
  scanId: string
  check: {
    id: string
    label: string
    category: string
    status: 'pass' | 'warn' | 'fail' | 'na'
    detail: string
    remediation?: string
    screenshot_url?: string
  }
  isPaidTier: boolean
  onScreenshotUploaded?: (checkId: string, url: string) => void
}

const statusConfig = {
  pass: {
    icon: (
      <svg className="w-5 h-5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
      </svg>
    ),
    bg: 'bg-green-50',
    border: 'border-green-200',
    badge: 'bg-green-100 text-green-700',
    label: 'Pass',
  },
  warn: {
    icon: (
      <svg className="w-5 h-5 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      </svg>
    ),
    bg: 'bg-amber-50',
    border: 'border-amber-200',
    badge: 'bg-amber-100 text-amber-700',
    label: 'Warning',
  },
  fail: {
    icon: (
      <svg className="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
      </svg>
    ),
    bg: 'bg-red-50',
    border: 'border-red-200',
    badge: 'bg-red-100 text-red-700',
    label: 'Failed',
  },
  na: {
    icon: (
      <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
      </svg>
    ),
    bg: 'bg-gray-50',
    border: 'border-gray-200',
    badge: 'bg-gray-100 text-gray-500',
    label: 'N/A',
  },
}

export default function CheckResult({ scanId, check, isPaidTier, onScreenshotUploaded }: CheckResultProps) {
  const config = statusConfig[check.status]
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [screenshotUrl, setScreenshotUrl] = useState<string | null>(check.screenshot_url ?? null)
  const [isExpanded, setIsExpanded] = useState(false)

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return

    if (!file.type.startsWith('image/')) {
      setUploadError('Please upload an image file (PNG, JPG, etc.)')
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      setUploadError('File must be under 10 MB')
      return
    }

    setUploading(true)
    setUploadError(null)
    try {
      const result = await uploadScreenshot(scanId, check.id, file) as { screenshot_url: string }
      setScreenshotUrl(result.screenshot_url)
      onScreenshotUploaded?.(check.id, result.screenshot_url)
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed. Please try again.')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className={`rounded-lg border ${config.border} ${config.bg} p-4`}>
      <div
        className="flex items-start gap-3 cursor-pointer"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex-shrink-0 mt-0.5">{config.icon}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-gray-900 text-sm">{check.label}</span>
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${config.badge}`}>
              {config.label}
            </span>
            <span className="text-xs text-gray-500 ml-auto">{check.category}</span>
          </div>
          <p className="text-sm text-gray-600 mt-1">{check.detail}</p>
        </div>
        <button className="flex-shrink-0 text-gray-400 hover:text-gray-600 ml-2">
          <svg
            className={`w-4 h-4 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </div>

      {isExpanded && (
        <div className="mt-4 ml-8 space-y-3">
          {/* Warning: screenshot upload */}
          {check.status === 'warn' && (
            <div className="rounded-md bg-white border border-amber-200 p-3">
              <p className="text-sm font-medium text-amber-800 mb-2">
                📸 Upload a screenshot to verify — we'll update your score
              </p>
              {screenshotUrl ? (
                <div className="space-y-2">
                  <img
                    src={screenshotUrl}
                    alt="Verification screenshot"
                    className="max-h-40 rounded border border-gray-200"
                  />
                  <p className="text-xs text-green-600 font-medium">✓ Screenshot submitted for review</p>
                </div>
              ) : (
                <label className="block">
                  <span className="sr-only">Upload screenshot</span>
                  <input
                    type="file"
                    accept="image/*"
                    onChange={handleFileChange}
                    disabled={uploading}
                    className="block w-full text-sm text-gray-500 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-amber-100 file:text-amber-700 hover:file:bg-amber-200 cursor-pointer"
                  />
                  {uploading && (
                    <p className="text-xs text-gray-500 mt-1">Uploading…</p>
                  )}
                  {uploadError && (
                    <p className="text-xs text-red-600 mt-1">{uploadError}</p>
                  )}
                </label>
              )}
            </div>
          )}

          {/* Fail: remediation */}
          {check.status === 'fail' && check.remediation && (
            <div className="rounded-md bg-white border border-red-200 p-3">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                How to fix this
              </p>
              {isPaidTier ? (
                <p className="text-sm text-gray-700">{check.remediation}</p>
              ) : (
                <div className="relative">
                  <p className="text-sm text-gray-700 filter blur-sm select-none">
                    {check.remediation}
                  </p>
                  <div className="absolute inset-0 flex items-center justify-center">
                    <a
                      href="/#pricing"
                      className="bg-brand-gold text-white text-xs font-semibold px-3 py-1.5 rounded-md shadow hover:bg-brand-gold-dark transition-colors"
                      onClick={(e) => e.stopPropagation()}
                    >
                      Upgrade to see fix →
                    </a>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
