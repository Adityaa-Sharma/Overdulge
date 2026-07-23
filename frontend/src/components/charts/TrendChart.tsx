import { formatPaise, formatShortDate } from '../../lib/format'

export interface TrendChartPoint {
  periodStart: string
  valuePaise: number
}

interface TrendChartProps {
  title: string
  points: TrendChartPoint[]
}

const CHART_HEIGHT = 140
const BAR_WIDTH = 28
const BAR_GAP = 12

/**
 * Minimal hand-built bar chart (no charting library — see the feature note
 * §5). Renders as an SVG for sighted users, plus a visually-hidden table
 * carrying the same figures for screen readers, since a handful of bars with
 * no axis ticks isn't reliably announced from SVG alone.
 */
export default function TrendChart({ title, points }: TrendChartProps) {
  const max = Math.max(1, ...points.map((point) => point.valuePaise))
  const width = Math.max(points.length * (BAR_WIDTH + BAR_GAP), 200)

  return (
    <div className="chart-trend">
      <h3>{title}</h3>
      <div className="chart-trend__scroll">
        <svg
          role="img"
          aria-label={`${title}: ${points.map((point) => `${formatShortDate(point.periodStart)} ${formatPaise(point.valuePaise)}`).join(', ')}`}
          width={width}
          height={CHART_HEIGHT + 24}
          viewBox={`0 0 ${width} ${CHART_HEIGHT + 24}`}
        >
          {points.map((point, index) => {
            const barHeight = Math.max(2, (point.valuePaise / max) * CHART_HEIGHT)
            const x = index * (BAR_WIDTH + BAR_GAP)
            return (
              <g key={point.periodStart}>
                <title>
                  {formatShortDate(point.periodStart)}: {formatPaise(point.valuePaise)}
                </title>
                <rect
                  x={x}
                  y={CHART_HEIGHT - barHeight}
                  width={BAR_WIDTH}
                  height={barHeight}
                  rx={4}
                  fill="url(#trend-bar-fill)"
                />
                <text
                  x={x + BAR_WIDTH / 2}
                  y={CHART_HEIGHT + 16}
                  textAnchor="middle"
                  fontSize="10"
                  fill="var(--ink-2)"
                >
                  {formatShortDate(point.periodStart)}
                </text>
              </g>
            )
          })}
          {/* The redesign renamed the palette tokens; the old --brand-*
              gradient stops resolved to nothing and every bar rendered black.
              These are the current marigold tokens. */}
          <defs>
            <linearGradient id="trend-bar-fill" x1="0" y1="1" x2="0" y2="0">
              <stop offset="0%" stopColor="var(--gold-deep)" />
              <stop offset="100%" stopColor="var(--gold)" />
            </linearGradient>
          </defs>
        </svg>
      </div>
      <table className="sr-only">
        <caption>{title}</caption>
        <thead>
          <tr>
            <th scope="col">Period</th>
            <th scope="col">Spend</th>
          </tr>
        </thead>
        <tbody>
          {points.map((point) => (
            <tr key={point.periodStart}>
              <td>{formatShortDate(point.periodStart)}</td>
              <td>{formatPaise(point.valuePaise)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
