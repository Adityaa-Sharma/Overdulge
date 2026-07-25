import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { getCalories, getCaloriesCommentary, type CaloriesResponse } from '../lib/api'
import { formatKcalEstimate } from '../lib/format'
import TrendChart from '../components/charts/TrendChart'

type LoadState = 'loading' | 'error' | 'ready'
type CommentaryState = 'idle' | 'loading' | 'error' | 'ready'

export default function Calories() {
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [data, setData] = useState<CaloriesResponse | null>(null)

  const [commentaryState, setCommentaryState] = useState<CommentaryState>('idle')
  const [blurb, setBlurb] = useState<string | null>(null)
  const [commentaryAttempt, setCommentaryAttempt] = useState(0)

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

  useEffect(() => {
    fetchCalories()
  }, [fetchCalories])

  // Lazy, non-blocking: fires only once the (fast, DB-only) totals/trend are
  // in and there's real data to comment on — never holds up the render above.
  useEffect(() => {
    if (loadState !== 'ready' || !data?.has_data) return

    let cancelled = false
    setCommentaryState('loading')
    getCaloriesCommentary()
      .then((response) => {
        if (cancelled) return
        setBlurb(response.blurb)
        setCommentaryState('ready')
      })
      .catch(() => {
        if (cancelled) return
        setCommentaryState('error')
      })
    return () => {
      cancelled = true
    }
  }, [loadState, data, commentaryAttempt])

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

      {loadState === 'ready' && data && !data.has_data && <EmptyState />}

      {loadState === 'ready' && data && data.has_data && (
        <PopulatedCalories
          data={data}
          commentaryState={commentaryState}
          blurb={blurb}
          onRetryCommentary={() => setCommentaryAttempt((attempt) => attempt + 1)}
        />
      )}
    </main>
  )
}

function EmptyState() {
  return (
    <section className="card card--pad-lg">
      <div className="empty">
        <span className="empty__icon" aria-hidden="true">
          🍽️
        </span>
        <h2>No orders to estimate yet</h2>
        <p>
          Once you link an account and sync your orders, Overdulge will estimate weekly calorie
          intake from your food deliveries — never from groceries you cook yourself.
        </p>
        <Link className="btn btn-primary" to="/settings">
          Connect an account
        </Link>
      </div>
    </section>
  )
}

function PopulatedCalories({
  data,
  commentaryState,
  blurb,
  onRetryCommentary,
}: {
  data: CaloriesResponse
  commentaryState: CommentaryState
  blurb: string | null
  onRetryCommentary: () => void
}) {
  return (
    <>
      <section className="grid" aria-label="Calorie estimate totals">
        <div className="card stat">
          <span className="stat__label">This week</span>
          <span className="stat__value">
            {formatKcalEstimate(data.totals.this_week_estimate_kcal)}
          </span>
        </div>
        <div className="card stat">
          <span className="stat__label">This month</span>
          <span className="stat__value">
            {formatKcalEstimate(data.totals.this_month_estimate_kcal)}
          </span>
        </div>
      </section>

      <section className="card card--pad-lg" aria-label="Calorie trend">
        <h2>Trend</h2>
        {data.trend.weekly.length === 0 ? (
          <p className="muted">No weekly trend yet.</p>
        ) : (
          <TrendChart
            title="Weekly estimated intake"
            points={data.trend.weekly.map((point) => ({
              periodStart: point.period_start,
              value: point.estimate_kcal,
            }))}
            formatValue={formatKcalEstimate}
            valueColumnLabel="Estimated intake"
          />
        )}
      </section>

      <section className="card card--pad-lg" aria-label="Weekly commentary">
        <h2>This week, roughly…</h2>

        {commentaryState === 'loading' && (
          <div className="skeleton" aria-busy="true" aria-label="Loading commentary" />
        )}

        {commentaryState === 'error' && (
          <div role="alert">
            <p>Couldn't load this week's commentary.</p>
            <button type="button" onClick={onRetryCommentary}>
              Retry
            </button>
          </div>
        )}

        {commentaryState === 'ready' && blurb && <p>{blurb}</p>}
      </section>
    </>
  )
}
