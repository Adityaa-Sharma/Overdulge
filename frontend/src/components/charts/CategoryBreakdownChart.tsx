import { formatPaise } from '../../lib/format'

export interface CategorySegment {
  label: string
  valuePaise: number
}

interface CategoryBreakdownChartProps {
  title: string
  segments: CategorySegment[]
}

/**
 * Minimal hand-built horizontal bar breakdown (no charting library — see the
 * feature note §5). Plain CSS bars rather than SVG since each row is already
 * an accessible list item with visible text — no separate text alternative
 * needed.
 */
export default function CategoryBreakdownChart({ title, segments }: CategoryBreakdownChartProps) {
  const max = Math.max(1, ...segments.map((segment) => segment.valuePaise))

  return (
    <div className="chart-breakdown">
      <h3>{title}</h3>
      <ul className="chart-breakdown__list">
        {segments.map((segment) => (
          <li key={segment.label}>
            <div className="chart-breakdown__row">
              <span>{segment.label}</span>
              <span>{formatPaise(segment.valuePaise)}</span>
            </div>
            <div className="chart-breakdown__track">
              <div
                className="chart-breakdown__fill"
                style={{ width: `${(segment.valuePaise / max) * 100}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
