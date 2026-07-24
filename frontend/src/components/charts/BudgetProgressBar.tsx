import type { BudgetProgress } from '../../lib/api'

interface BudgetProgressBarProps {
  status: BudgetProgress['status']
  pct: number
}

/**
 * Minimal hand-built progress bar (no charting library — see the dashboard
 * feature note §5), reusing the dashboard's chart-breakdown track/fill
 * pattern with a status-coloured fill instead of the flat gold gradient.
 * Purely decorative: the spent/cap figures and status badge rendered
 * alongside it already carry the same information as text.
 */
export default function BudgetProgressBar({ status, pct }: BudgetProgressBarProps) {
  const widthPct = Math.min(100, Math.max(0, pct * 100))
  return (
    <div className="chart-breakdown__track" aria-hidden="true">
      <div
        className={`chart-breakdown__fill chart-breakdown__fill--${status}`}
        style={{ width: `${widthPct}%` }}
      />
    </div>
  )
}
