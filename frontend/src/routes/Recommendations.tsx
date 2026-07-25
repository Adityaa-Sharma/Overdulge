import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import {
  getSuggestions,
  getUsuals,
  type RecommendationPlatform,
  type Suggestion,
  type UsualItem,
  type UsualsResponse,
} from '../lib/api'
import { formatKcalEstimate, formatPaise } from '../lib/format'

type LoadState = 'loading' | 'error' | 'ready'
type SuggestionsState = 'idle' | 'loading' | 'error' | 'ready'

const PLATFORMS: RecommendationPlatform[] = ['swiggy_food', 'swiggy_instamart', 'zepto']

const PLATFORM_LABELS: Record<RecommendationPlatform, string> = {
  swiggy_food: 'Swiggy Food',
  swiggy_instamart: 'Swiggy Instamart',
  zepto: 'Zepto',
}

const PLATFORM_COLORS: Record<RecommendationPlatform, string> = {
  swiggy_food: 'var(--swiggy)',
  swiggy_instamart: 'var(--instamart)',
  zepto: 'var(--zepto)',
}

function suggestionKey(platform: RecommendationPlatform, key: string): string {
  return `${platform}:${key}`
}

/** Joins the price and calorie figures a usual/alternative carries; either may be absent (AC-5, FR-6 scope). */
function itemMeta(unitPricePaise: number | null, calorieEstimate: number | null): string | null {
  const parts: string[] = []
  if (unitPricePaise !== null) parts.push(formatPaise(unitPricePaise))
  if (calorieEstimate !== null) parts.push(formatKcalEstimate(calorieEstimate))
  return parts.length > 0 ? parts.join(' · ') : null
}

export default function Recommendations() {
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [usuals, setUsuals] = useState<UsualsResponse | null>(null)

  const [suggestionsState, setSuggestionsState] = useState<SuggestionsState>('idle')
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [suggestionsAttempt, setSuggestionsAttempt] = useState(0)

  const fetchUsuals = useCallback(async () => {
    setLoadState('loading')
    try {
      const response = await getUsuals()
      setUsuals(response)
      setLoadState('ready')
    } catch {
      setLoadState('error')
    }
  }, [])

  useEffect(() => {
    fetchUsuals()
  }, [fetchUsuals])

  const hasUsuals = Boolean(usuals && PLATFORMS.some((platform) => usuals[platform].length > 0))

  // Suggestions call a live search per frequent item (ADR-0004 §2 Consequences),
  // so they're fetched lazily once usuals have rendered rather than blocking
  // the primary view on the slowest live search call. `suggestionsState` is
  // deliberately left out of the dependency array: setting it to 'loading'
  // inside this effect must not itself re-trigger the effect (that would
  // cancel the in-flight fetch before it ever resolves) — re-fetching only
  // happens on a real usuals reload or an explicit retry (`suggestionsAttempt`).
  useEffect(() => {
    if (loadState !== 'ready' || !hasUsuals) return

    let cancelled = false
    setSuggestionsState('loading')
    getSuggestions()
      .then((response) => {
        if (cancelled) return
        setSuggestions(response.suggestions)
        setSuggestionsState('ready')
      })
      .catch(() => {
        if (cancelled) return
        setSuggestionsState('error')
      })
    return () => {
      cancelled = true
    }
  }, [loadState, hasUsuals, suggestionsAttempt])

  const suggestionsByKey = useMemo(
    () =>
      new Map(
        suggestions.map((suggestion) => [
          suggestionKey(suggestion.platform, suggestion.frequent_item.key),
          suggestion,
        ]),
      ),
    [suggestions],
  )

  return (
    <main>
      <div className="page-head">
        <span className="eyebrow">Smarter reorders</span>
        <h1>Usuals &amp; recommendations</h1>
      </div>

      {loadState === 'loading' && (
        <section aria-busy="true" aria-label="Loading usuals">
          <div className="grid">
            <div className="skeleton" />
            <div className="skeleton" />
            <div className="skeleton" />
          </div>
        </section>
      )}

      {loadState === 'error' && (
        <section className="card card--pad-lg">
          <div role="alert">
            <p>Couldn't load your usuals.</p>
            <button type="button" onClick={fetchUsuals}>
              Retry
            </button>
          </div>
        </section>
      )}

      {loadState === 'ready' && usuals && !hasUsuals && <EmptyState />}

      {loadState === 'ready' && usuals && hasUsuals && (
        <PopulatedRecommendations
          usuals={usuals}
          suggestionsByKey={suggestionsByKey}
          suggestionsState={suggestionsState}
          onRetrySuggestions={() => setSuggestionsAttempt((attempt) => attempt + 1)}
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
          🔁
        </span>
        <h2>No usuals yet</h2>
        <p>
          Once you link an account and sync your orders, Overdulge will surface your most-ordered
          items here — with a cheaper or lower-calorie alternative alongside, whenever live search
          turns one up.
        </p>
        <Link className="btn btn-primary" to="/settings">
          Connect an account
        </Link>
      </div>
    </section>
  )
}

function PopulatedRecommendations({
  usuals,
  suggestionsByKey,
  suggestionsState,
  onRetrySuggestions,
}: {
  usuals: UsualsResponse
  suggestionsByKey: Map<string, Suggestion>
  suggestionsState: SuggestionsState
  onRetrySuggestions: () => void
}) {
  const platformsWithUsuals = PLATFORMS.filter((platform) => usuals[platform].length > 0)

  return (
    <>
      {suggestionsState === 'loading' && (
        <p className="muted" aria-live="polite">
          Checking live prices for cheaper or lower-calorie alternatives…
        </p>
      )}

      {suggestionsState === 'error' && (
        <section className="card card--pad-lg">
          <div role="alert">
            <p>Couldn't load suggested alternatives — your usuals are still shown below.</p>
            <button type="button" onClick={onRetrySuggestions}>
              Retry
            </button>
          </div>
        </section>
      )}

      {platformsWithUsuals.map((platform) => (
        <section
          key={platform}
          className="card card--pad-lg"
          aria-label={`${PLATFORM_LABELS[platform]} usuals`}
        >
          <h2>
            <span
              className="platform-dot"
              aria-hidden="true"
              style={{ background: PLATFORM_COLORS[platform] }}
            />
            {PLATFORM_LABELS[platform]}
          </h2>
          <ol className="chart-breakdown__list">
            {usuals[platform].map((item) => (
              <UsualRow
                key={item.key}
                platform={platform}
                item={item}
                suggestion={suggestionsByKey.get(suggestionKey(platform, item.key))}
              />
            ))}
          </ol>
        </section>
      ))}
    </>
  )
}

function UsualRow({
  platform,
  item,
  suggestion,
}: {
  platform: RecommendationPlatform
  item: UsualItem
  suggestion?: Suggestion
}) {
  const meta = itemMeta(item.avg_unit_price_paise, item.calorie_estimate)
  const alternative = suggestion?.alternative
  const alternativeMeta = alternative
    ? itemMeta(alternative.unit_price_paise, alternative.calorie_estimate)
    : null

  return (
    <li>
      <div className="chart-breakdown__row">
        <span>{item.name}</span>
        <a
          className="btn btn-ghost"
          href={item.redirect_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Reorder ${item.name} on ${PLATFORM_LABELS[platform]}`}
        >
          Reorder
        </a>
      </div>
      {meta && <p className="muted">{meta}</p>}

      {alternative && (
        <div className="suggestion">
          <div className="chart-breakdown__row">
            <span className={`badge ${alternative.cheaper ? 'badge-good' : 'badge-idle'}`}>
              {alternative.cheaper ? 'Cheaper' : 'Lower calorie'}
            </span>
          </div>
          <div className="chart-breakdown__row">
            <span>Try instead: {alternative.name}</span>
            <a
              className="btn btn-primary"
              href={alternative.redirect_url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Reorder ${alternative.name} instead of ${item.name}`}
            >
              Reorder
            </a>
          </div>
          {alternativeMeta && <p className="muted">{alternativeMeta}</p>}
        </div>
      )}
    </li>
  )
}
