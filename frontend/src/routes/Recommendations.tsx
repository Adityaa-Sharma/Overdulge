import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
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

const PLATFORM_LABELS: Record<RecommendationPlatform, string> = {
  zepto: 'Zepto',
  swiggy_instamart: 'Swiggy Instamart',
  swiggy_food: 'Swiggy Food',
}

const PLATFORMS: RecommendationPlatform[] = ['zepto', 'swiggy_instamart', 'swiggy_food']

/** Keys a suggestion by the platform + frequent-item key it was matched against, so the populated list can look one up per usual in O(1) (AC-5: an item with no key here renders with no suggestion slot at all). */
function keySuggestions(suggestions: Suggestion[]): Map<string, Suggestion> {
  return new Map(suggestions.map((s) => [`${s.platform}:${s.frequent_item.key}`, s]))
}

export default function Recommendations() {
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [usuals, setUsuals] = useState<UsualsResponse | null>(null)

  const [suggestionsState, setSuggestionsState] = useState<SuggestionsState>('idle')
  const [suggestionsByKey, setSuggestionsByKey] = useState<Map<string, Suggestion>>(new Map())
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

  const hasAnyUsuals = Boolean(
    usuals && PLATFORMS.some((platform) => usuals[platform].length > 0),
  )

  // Suggestions need a live search call per frequent item (ADR-0004 §2), so
  // they're fetched lazily once usuals have rendered — same lazy pattern as
  // Calories' commentary. Retries bump `suggestionsAttempt` rather than
  // reset `suggestionsState` itself, so this effect's own 'loading' update
  // can't retrigger it mid-fetch and cancel the request that's still in flight.
  useEffect(() => {
    if (loadState !== 'ready' || !hasAnyUsuals) return

    let cancelled = false
    setSuggestionsState('loading')
    getSuggestions()
      .then((response) => {
        if (cancelled) return
        setSuggestionsByKey(keySuggestions(response.suggestions))
        setSuggestionsState('ready')
      })
      .catch(() => {
        if (cancelled) return
        setSuggestionsState('error')
      })
    return () => {
      cancelled = true
    }
  }, [loadState, hasAnyUsuals, suggestionsAttempt])

  return (
    <main>
      <div className="page-head">
        <span className="eyebrow">Order less, order smarter</span>
        <h1>Usuals &amp; recommendations</h1>
      </div>

      {loadState === 'loading' && (
        <section aria-busy="true" aria-label="Loading your usuals">
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

      {loadState === 'ready' && usuals && !hasAnyUsuals && <EmptyState />}

      {loadState === 'ready' && usuals && hasAnyUsuals && (
        <PopulatedRecommendations
          usuals={usuals}
          suggestionsState={suggestionsState}
          suggestionsByKey={suggestionsByKey}
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
          items here, with a cheaper or lower-calorie alternative alongside any that qualify.
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
  suggestionsState,
  suggestionsByKey,
  onRetrySuggestions,
}: {
  usuals: UsualsResponse
  suggestionsState: SuggestionsState
  suggestionsByKey: Map<string, Suggestion>
  onRetrySuggestions: () => void
}) {
  return (
    <>
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

      {PLATFORMS.filter((platform) => usuals[platform].length > 0).map((platform) => (
        <PlatformSection
          key={platform}
          platform={platform}
          items={usuals[platform]}
          suggestionsState={suggestionsState}
          suggestionsByKey={suggestionsByKey}
        />
      ))}
    </>
  )
}

function PlatformSection({
  platform,
  items,
  suggestionsState,
  suggestionsByKey,
}: {
  platform: RecommendationPlatform
  items: UsualItem[]
  suggestionsState: SuggestionsState
  suggestionsByKey: Map<string, Suggestion>
}) {
  const label = PLATFORM_LABELS[platform]
  return (
    <section className="card card--pad-lg" aria-label={`${label} usuals`}>
      <h2>{label}</h2>
      <ol className="link-list">
        {items.map((item, index) => {
          const suggestion = suggestionsByKey.get(`${platform}:${item.key}`)
          return (
            <li key={item.key}>
              <div>
                <h3>
                  Usual #{index + 1}: {item.name}
                </h3>
                <p className="muted">
                  {item.avg_unit_price_paise !== null ? formatPaise(item.avg_unit_price_paise) : '—'}
                  {item.calorie_estimate !== null
                    ? ` · ${formatKcalEstimate(item.calorie_estimate)}`
                    : ''}
                </p>

                {suggestionsState === 'loading' && !suggestion && (
                  <p className="muted" aria-busy="true">
                    Checking for a cheaper or lower-calorie alternative…
                  </p>
                )}

                {suggestion && <SuggestionNote suggestion={suggestion} />}
              </div>

              <div className="row">
                <a
                  className="btn-ghost"
                  href={item.redirect_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Reorder
                </a>
                {suggestion && (
                  <a
                    className="btn-primary"
                    href={suggestion.alternative.redirect_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Reorder {suggestion.alternative.name} instead
                  </a>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function SuggestionNote({ suggestion }: { suggestion: Suggestion }) {
  const { alternative } = suggestion
  return (
    <p>
      <span className="badge badge-good">
        {alternative.cheaper && alternative.lower_calorie
          ? 'Cheaper & lower-calorie'
          : alternative.cheaper
            ? 'Cheaper'
            : 'Lower-calorie'}
      </span>{' '}
      Try <strong>{alternative.name}</strong>
      {alternative.unit_price_paise !== null ? ` — ${formatPaise(alternative.unit_price_paise)}` : ''}
      {alternative.calorie_estimate !== null
        ? ` · ${formatKcalEstimate(alternative.calorie_estimate)}`
        : ''}
    </p>
  )
}
