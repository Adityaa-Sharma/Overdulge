import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getDashboard, type DashboardResponse, type SyncPlatform } from '../lib/api'
import { formatPaise } from '../lib/format'
import CategoryBreakdownChart from '../components/charts/CategoryBreakdownChart'
import TrendChart from '../components/charts/TrendChart'

type LoadState = 'loading' | 'error' | 'ready'

const PLATFORM_LABELS: Record<SyncPlatform, string> = {
  swiggy_food: 'Swiggy Food',
  swiggy_instamart: 'Swiggy Instamart',
  zepto: 'Zepto',
}

const PLATFORMS: SyncPlatform[] = ['swiggy_food', 'swiggy_instamart', 'zepto']

const GHOST_ROWS = [
  { name: 'SWIGGY FOOD', qty: '14x', amount: '4,930', color: 'var(--swiggy)' },
  { name: 'INSTAMART', qty: '11x', amount: '3,820', color: 'var(--instamart)' },
  { name: 'ZEPTO', qty: '9x', amount: '3,730', color: 'var(--zepto)' },
]

const PROMISES = [
  { icon: '🧾', label: 'One itemised receipt' },
  { icon: '🌙', label: 'Late-night patterns' },
  { icon: '🎯', label: 'Budgets that nudge' },
]

export default function Dashboard() {
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [data, setData] = useState<DashboardResponse | null>(null)

  const fetchDashboard = useCallback(async () => {
    setLoadState('loading')
    try {
      const response = await getDashboard()
      setData(response)
      setLoadState('ready')
    } catch {
      setLoadState('error')
    }
  }, [])

  useEffect(() => {
    fetchDashboard()
  }, [fetchDashboard])

  return (
    <main>
      <div className="page-head">
        <span className="eyebrow">Your spend</span>
        <h1>Dashboard</h1>
      </div>

      {loadState === 'loading' && (
        <section aria-busy="true" aria-label="Loading dashboard">
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
            <p>Couldn't load your dashboard.</p>
            <button type="button" onClick={fetchDashboard}>
              Retry
            </button>
          </div>
        </section>
      )}

      {loadState === 'ready' && data && !data.has_data && <OnboardingEmptyState />}

      {loadState === 'ready' && data && data.has_data && <PopulatedDashboard data={data} />}
    </main>
  )
}

/**
 * Zero-data screen, built as an onboarding surface rather than a dead end.
 *
 * Empty-state research is consistent that first-run screens should name the
 * value and show the SHAPE of success, not announce absence — so this leads
 * with what the user gets and previews a muted sample receipt. Finance
 * onboarding also converts on trust, hence the read-only promise sits right
 * at the point of decision.
 */
function OnboardingEmptyState() {
  return (
    <>
      <section className="card card--pad-lg" aria-labelledby="onboarding-heading">
        <div className="empty">
          <span className="empty__icon" aria-hidden="true">
            🧾
          </span>

          <h2 id="onboarding-heading">
            See exactly where your <span className="hl">food money</span> goes
          </h2>
          <p>
            Connect Swiggy or Zepto and Overdulge turns your order history into one itemised
            monthly receipt — what you spent, on which app, and the habits hiding behind it.
          </p>

          <Link className="btn btn-primary" to="/settings">
            Connect your first account
          </Link>

          <p className="auth__trust">
            Read-only access — Overdulge can never place, change, or cancel an order.
          </p>

          {/* Shape of success. Decorative sample figures, so it is hidden from
              assistive tech: screen readers must never announce fake spend. */}
          <div className="ghost" aria-hidden="true">
            <div className="receipt">
              <div className="receipt__head">
                <div className="receipt__store">OVERDULGE</div>
                <div className="receipt__meta">YOUR MONTH, ITEMISED</div>
              </div>
              <hr />
              {GHOST_ROWS.map((row) => (
                <div className="r-line" key={row.name}>
                  <span className="dot" style={{ background: row.color }} />
                  <span className="nm">{row.name}</span>
                  <span className="qty">{row.qty}</span>
                  <span className="pr">{row.amount}</span>
                </div>
              ))}
              <hr className="double" />
              <div className="r-total">
                <span>TOTAL</span>
                <span className="v">₹12,480</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <ul className="row row--center" aria-label="What you get once an account is linked">
        {PROMISES.map((promise) => (
          <li className="sticker" key={promise.label}>
            <span aria-hidden="true">{promise.icon}</span>
            {promise.label}
          </li>
        ))}
      </ul>
    </>
  )
}

function PopulatedDashboard({ data }: { data: DashboardResponse }) {
  return (
    <>
      <section className="grid" aria-label="Spend totals">
        <div className="card stat">
          <span className="stat__label">This week</span>
          <span className="stat__value">{formatPaise(data.totals.this_week_paise.combined)}</span>
        </div>
        <div className="card stat">
          <span className="stat__label">This month</span>
          <span className="stat__value">
            {formatPaise(data.totals.this_month_paise.combined)}
          </span>
        </div>
        <div className="card stat">
          <span className="stat__label">Projection ({data.projection.month})</span>
          <span className="stat__value">
            {formatPaise(data.projection.projected_total_paise.combined)}
          </span>
          <span className="stat__delta muted">
            {data.projection.label} · day {data.projection.days_elapsed} of{' '}
            {data.projection.days_in_month}
          </span>
        </div>
      </section>

      <section className="card card--pad-lg" aria-label="Spend by platform">
        <h2>Spend by platform</h2>
        <ul className="chart-breakdown__list">
          {PLATFORMS.map((platform) => (
            <li key={platform}>
              <div className="chart-breakdown__row">
                <span>{PLATFORM_LABELS[platform]}</span>
                <span>{formatPaise(data.totals.this_month_paise[platform])}</span>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="card card--pad-lg" aria-label="Spend trend">
        <h2>Trend</h2>
        <TrendChart
          title="Weekly spend"
          points={data.trend.weekly.map((point) => ({
            periodStart: point.period_start,
            value: point.combined_paise,
          }))}
        />
        <TrendChart
          title="Monthly spend"
          points={data.trend.monthly.map((point) => ({
            periodStart: point.period_start,
            value: point.combined_paise,
          }))}
        />
      </section>

      <section className="card card--pad-lg" aria-label="Category breakdown">
        <h2>Category breakdown</h2>
        <CategoryBreakdownChart
          title="Food delivery vs grocery"
          segments={[
            { label: 'Food delivery', valuePaise: data.category_breakdown.food_delivery_paise },
            { label: 'Grocery', valuePaise: data.category_breakdown.grocery_paise },
          ]}
        />
        {Object.keys(data.category_breakdown.item_categories_paise).length > 0 && (
          <CategoryBreakdownChart
            title="By item category"
            segments={Object.entries(data.category_breakdown.item_categories_paise).map(
              ([label, valuePaise]) => ({ label, valuePaise }),
            )}
          />
        )}
      </section>

      <section className="grid" aria-label="Top restaurants and products">
        <div className="card card--pad-lg">
          <h2>Top restaurants</h2>
          {data.top_restaurants.length === 0 ? (
            <p className="muted">No restaurant spend yet.</p>
          ) : (
            <ol className="link-list">
              {data.top_restaurants.map((entry) => (
                <li key={entry.name}>
                  <div>
                    <h3>{entry.name}</h3>
                    <p className="muted">{entry.order_count} orders</p>
                  </div>
                  <span className="stat__value" style={{ fontSize: '1.1rem' }}>
                    {formatPaise(entry.spend_paise)}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
        <div className="card card--pad-lg">
          <h2>Top products</h2>
          {data.top_products.length === 0 ? (
            <p className="muted">No product spend yet.</p>
          ) : (
            <ol className="link-list">
              {data.top_products.map((entry) => (
                <li key={entry.name}>
                  <div>
                    <h3>{entry.name}</h3>
                    <p className="muted">{entry.order_count} orders</p>
                  </div>
                  <span className="stat__value" style={{ fontSize: '1.1rem' }}>
                    {formatPaise(entry.spend_paise)}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </section>

      <section className="card card--pad-lg" aria-label="Order stats">
        <h2>Order stats</h2>
        <ul className="link-list">
          {PLATFORMS.map((platform) => {
            const stats = data.order_stats[platform]
            return (
              <li key={platform}>
                <div>
                  <h3>{PLATFORM_LABELS[platform]}</h3>
                  <p className="muted">{stats.order_count} orders</p>
                </div>
                <span>
                  {stats.avg_order_value_paise === null
                    ? '—'
                    : `${formatPaise(stats.avg_order_value_paise)} avg`}
                </span>
              </li>
            )
          })}
        </ul>
      </section>

      {data.location_lens.length > 0 && (
        <section className="card card--pad-lg" aria-label="Spend by location">
          <h2>Spend by location</h2>
          <ul className="link-list">
            {data.location_lens.map((entry) => (
              <li key={entry.address_id}>
                <div>
                  <h3>{entry.address_label ?? 'Saved address'}</h3>
                  <p className="muted">{entry.order_count} orders</p>
                </div>
                <span>{formatPaise(entry.spend_paise)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}
