import { useCallback, useEffect, useState } from 'react'
import { getCalories, getCaloriesCommentary, type CaloriesResponse } from '../lib/api'
import { formatKcalEstimate } from '../lib/format'
import TrendChart from '../components/charts/TrendChart'

type LoadState = 'loading' | 'error' | 'ready'
type CommentaryState = 'loading' | 'error' | 'ready'

export default function Calories() {
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [data, setData] = useState<CaloriesResponse | null>(null)

  const [commentaryState, setCommentaryState] = useState<CommentaryState>('loading')
  const [blurb, setBlurb] = useState<string | null>(null)

  const fetchCalories = useCallback(async () => {
    setLoadState('loading')
    try {
      const response = await getCalories()
      setData(response)
      setLoadState('ready')
    } catch {
      setLoadState('error')
    }
  }, [])

  const fetchCommentary = useCallback(async () => {
    setCommentaryState('loading')
    try {
      const response = await getCaloriesCommentary()
      setBlurb(response.blurb)
      setCommentaryState('ready')
    } catch {
      setCommentaryState('error')
    }
  }, [])

  // The commentary call is intentionally independent of the totals/trend
  // fetch above — a slow or failing LLM round-trip must never block the
  // (fast, DB-only) rollup render (ADR-0008 §5).
  useEffect(() => {
    fetchCalories()
  }, [fetchCalories])

  useEffect(() => {
    fetchCommentary()
  }, [fetchCommentary])

  return (
    <main>
      <div className="page-head">
        <span className="eyebrow">Lighthearted, not judgmental</span>
        <h1>Calories</h1>
      </div>

      {loadState === 'loading' && (
        <section aria-busy="true" aria-label="Loading calorie estimates">
          <div className="grid">
            <div className="skeleton" />
            <div className="skeleton" />
          </div>
        </section>
      )}

      {loadState === 'error' && (
        <section className="card card--pad-lg">
          <div role="alert">
            <p>Couldn't load your calorie estimates.</p>
            <button type="button" onClick={fetchCalories}>
              Retry
            </button>
          </div>
        </section>
      )}

      {loadState === 'ready' && data && !data.has_data && (
        <section className="card card--pad-lg">
          <div className="empty">
            <span className="empty__icon" aria-hidden="true">
              🍛
            </span>
            <h2>No estimates yet</h2>
            <p>
              Once your first order syncs in, Overdulge will show a playful, estimated read on
              your weekly intake here.
            </p>
          </div>
        </section>
      )}

      {loadState === 'ready' && data && data.has_data && <PopulatedCalories data={data} />}

      {loadState === 'ready' && data && (
        <section className="card card--pad-lg" aria-label="Weekly commentary">
          <h2>This week, in a nutshell</h2>

          {commentaryState === 'loading' && (
            <div aria-busy="true" aria-label="Loading commentary">
              <div className="skeleton" />
            </div>
          )}

          {commentaryState === 'error' && (
            <div role="alert">
              <p>Couldn't load this week's commentary.</p>
              <button type="button" onClick={fetchCommentary}>
                Retry
              </button>
            </div>
          )}

          {commentaryState === 'ready' && blurb && <p>{blurb}</p>}
        </section>
      )}
    </main>
  )
}

function PopulatedCalories({ data }: { data: CaloriesResponse }) {
  return (
    <>
      <section className="grid" aria-label="Calorie totals">
        <div className="card stat">
          <span className="stat__label">This week (estimate)</span>
          <span className="stat__value">
            {formatKcalEstimate(data.totals.this_week_estimate_kcal)}
          </span>
        </div>
        <div className="card stat">
          <span className="stat__label">This month (estimate)</span>
          <span className="stat__value">
            {formatKcalEstimate(data.totals.this_month_estimate_kcal)}
          </span>
        </div>
      </section>

      <section className="card card--pad-lg" aria-label="Calorie trend">
        <h2>Trend</h2>
        <TrendChart
          title="Weekly estimated intake"
          points={data.trend.weekly.map((point) => ({
            periodStart: point.period_start,
            value: point.estimate_kcal,
          }))}
          formatValue={formatKcalEstimate}
          valueLabel="Estimated intake"
        />
      </section>
    </>
  )
}
