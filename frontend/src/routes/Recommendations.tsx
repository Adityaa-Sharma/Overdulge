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

const PLATFORM_ORDER: RecommendationPlatform[] = ['zepto', 'swiggy_instamart', 'swiggy_food']

const PLATFORM_LABELS: Record<RecommendationPlatform, string> = {
  zepto: 'Zepto',
  swiggy_instamart: 'Instamart',
  swiggy_food: 'Swiggy Food',
}

function suggestionMapKey(platform: RecommendationPlatform, key: string): string {
  return `${platform}:${key}`
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

  const hasUsuals =
    usuals !== null && PLATFORM_ORDER.some((platform) => usuals[platform].length > 0)

  // Lazy, non-blocking: fires only once usuals are in and there's something
  // to attach a suggestion to — mirrors Calories' commentary fetch, since
  // suggestions (unlike usuals) fan out to a live search call per item and
  // must never hold up the primary render.
  useEffect(() => {
    if (loadState !== 'ready' || !hasUsuals) return

    let cancelled = false
    setSuggestionsState('loading')
    getSuggestions()
      .then((response) => {
        if (cancelled) return
        const byKey = new Map<string, Suggestion>()
        for (const suggestion of response.suggestions) {
          byKey.set(suggestionMapKey(suggestion.platform, suggestion.frequent_item.key), suggestion)
        }
        setSuggestionsByKey(byKey)
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

  return (
    <main>
      <div className="page-head">
        <span className="eyebrow">Reorder in one tap</span>
        <h1>Usuals & recommendations</h1>
        <p className="muted">
          Your most-ordered items across every linked platform, with a cheaper or lower-calorie
          swap alongside when we find one. Reorder opens the item on the platform's own site —
          Overdulge never places an order for you.
        </p>
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

      {loadState === 'ready' && usuals && !hasUsuals && <EmptyState />}

      {loadState === 'ready' && usuals && hasUsuals && (
        <>
          {suggestionsState === 'error' && (
            <p role="alert">
              Couldn't load suggested alternatives.{' '}
              <button
                type="button"
                onClick={() => setSuggestionsAttempt((attempt) => attempt + 1)}
              >
                Retry
              </button>
            </p>
          )}

          {PLATFORM_ORDER.filter((platform) => usuals[platform].length > 0).map((platform) => (
            <PlatformSection
              key={platform}
              platform={platform}
              items={usuals[platform]}
              suggestionsByKey={suggestionsByKey}
              suggestionsLoading={suggestionsState === 'loading'}
            />
          ))}
        </>
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
          Once you link an account and sync your order history, Overdulge will surface your
          most-ordered items here, with a cheaper or lighter swap when we find one.
        </p>
        <Link className="btn btn-primary" to="/settings">
          Connect an account
        </Link>
      </div>
    </section>
  )
}

function PlatformSection({
  platform,
  items,
  suggestionsByKey,
  suggestionsLoading,
}: {
  platform: RecommendationPlatform
  items: UsualItem[]
  suggestionsByKey: Map<string, Suggestion>
  suggestionsLoading: boolean
}) {
  return (
    <section aria-label={`${PLATFORM_LABELS[platform]} usuals`}>
      <h2>{PLATFORM_LABELS[platform]}</h2>
      <ul className="link-list">
        {items.map((item) => {
          const suggestion = suggestionsByKey.get(suggestionMapKey(platform, item.key))
          return (
            <li key={item.key}>
              <div>
                <h3>{item.name}</h3>
                <p>{describeUsual(item)}</p>

                {suggestionsLoading && (
                  <p className="muted">Looking for a cheaper or lighter swap…</p>
                )}
                {!suggestionsLoading && suggestion && <SuggestionBlock suggestion={suggestion} />}
              </div>
              <a className="btn btn-primary" href={item.redirect_url} target="_blank" rel="noreferrer">
                Reorder
              </a>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function describeUsual(item: UsualItem): string {
  const parts: string[] = []
  if (item.avg_unit_price_paise !== null) parts.push(formatPaise(item.avg_unit_price_paise))
  if (item.calorie_estimate !== null) parts.push(formatKcalEstimate(item.calorie_estimate))
  return parts.length > 0 ? parts.join(' · ') : `Ordered ${item.frequency_rank_or_count} times`
}

function SuggestionBlock({ suggestion }: { suggestion: Suggestion }) {
  const { alternative } = suggestion
  const reason =
    alternative.cheaper && alternative.lower_calorie
      ? 'Cheaper & lower-calorie'
      : alternative.cheaper
        ? 'Cheaper'
        : 'Lower-calorie'
  const details: string[] = []
  if (alternative.unit_price_paise !== null) details.push(formatPaise(alternative.unit_price_paise))
  if (alternative.calorie_estimate !== null) details.push(formatKcalEstimate(alternative.calorie_estimate))

  return (
    <div className="suggestion" aria-label="Suggested alternative">
      <span className="badge badge-good">{reason} swap</span>
      <p>
        {alternative.name}
        {details.length > 0 && ` · ${details.join(' · ')}`}
      </p>
      <a
        className="btn btn-ghost"
        href={alternative.redirect_url}
        target="_blank"
        rel="noreferrer"
      >
        Reorder alternative
      </a>
    </div>
  )
}
