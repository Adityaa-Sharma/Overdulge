import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getDashboard, type DashboardResponse, type SyncPlatform } from '../lib/api'
import { formatPaise } from '../lib/format'
import { useSession } from '../lib/session'
import CategoryBreakdownChart from '../components/charts/CategoryBreakdownChart'
import TrendChart from '../components/charts/TrendChart'

type LoadState = 'loading' | 'error' | 'ready'

const PLATFORM_LABELS: Record<SyncPlatform, string> = {
  swiggy_food: 'Swiggy Food',
  swiggy_instamart: 'Swiggy Instamart',
  zepto: 'Zepto',
}

const PLATFORMS: SyncPlatform[] = ['swiggy_food', 'swiggy_instamart', 'zepto']

export default function Dashboard() {
  const { logout } = useSession()
  const [loggingOut, setLoggingOut] = useState(false)
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

  async function handleLogout() {
    setLoggingOut(true)
    try {
      await logout()
    } finally {
      setLoggingOut(false)
    }
  }

  return (
    <>
      <header className="app-header">
        <span className="brand">
          <span className="brand__mark" aria-hidden="true" />
          Overdulge
        </span>
        <nav className="app-nav">
          <Link to="/" aria-current="page">
            Dashboard
          </Link>
          <Link to="/settings">Settings</Link>
          <button className="btn-ghost" type="button" onClick={handleLogout} disabled={loggingOut}>
            {loggingOut ? 'Logging out…' : 'Log out'}
          </button>
        </nav>
      </header>

      <div className="container">
        <main>
          <div className="stack" style={{ gap: 4 }}>
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

          {loadState === 'ready' && data && !data.has_data && (
            <section className="card card--pad-lg">
              <div className="empty">
                <span className="empty__icon" aria-hidden="true" />
                <h2>No data yet</h2>
                <p>
                  Connect your Swiggy or Zepto account and Overdulge will sync your order
                  history, then break down where your money and calories are going.
                </p>
                <Link className="btn btn-primary" to="/settings">
                  Link an account
                </Link>
              </div>
            </section>
          )}

          {loadState === 'ready' && data && data.has_data && <PopulatedDashboard data={data} />}
        </main>
      </div>
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
            valuePaise: point.combined_paise,
          }))}
        />
        <TrendChart
          title="Monthly spend"
          points={data.trend.monthly.map((point) => ({
            periodStart: point.period_start,
            valuePaise: point.combined_paise,
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
                  <h3>Address {entry.address_id}</h3>
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
