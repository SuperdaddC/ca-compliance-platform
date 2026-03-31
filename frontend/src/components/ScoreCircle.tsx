interface ScoreCircleProps {
  score: number
  size?: number
}

export default function ScoreCircle({ score, size = 160 }: ScoreCircleProps) {
  const r = size * 0.34
  const cx = size / 2
  const cy = size / 2
  const circ = 2 * Math.PI * r
  const fill = circ * (score / 100)
  const color = score >= 80 ? '#16a34a' : score >= 60 ? '#d97706' : '#dc2626'
  const label = score >= 80 ? 'Good Standing' : score >= 60 ? 'Needs Attention' : 'Action Required'

  return (
    <div className="flex flex-col items-center gap-1.5">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#e5e7eb" strokeWidth="10" />
        <circle
          cx={cx} cy={cy} r={r}
          fill="none"
          stroke={color}
          strokeWidth="10"
          strokeDasharray={`${fill} ${circ}`}
          strokeLinecap="round"
          transform={`rotate(-90 ${cx} ${cy})`}
        />
        <text x={cx} y={cy - 6} textAnchor="middle" fontSize="28" fontWeight="700" fill={color}>
          {score}
        </text>
        <text x={cx} y={cy + 14} textAnchor="middle" fontSize="11" fill="#6b7280">
          /100
        </text>
      </svg>
      <span className="text-xs font-semibold" style={{ color }}>{label}</span>
    </div>
  )
}
