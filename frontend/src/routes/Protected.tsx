import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useSession } from '../lib/session'

export default function Protected() {
  const { logout } = useSession()
  const [loggingOut, setLoggingOut] = useState(false)

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

          {/* Preview of the metric layout the sync will fill. */}
          <section className="grid" aria-label="Spend summary">
            <div className="card stat">
              <span className="stat__label">This month</span>
              <span className="stat__value muted">—</span>
              <span className="stat__delta">Link an account to see spend</span>
            </div>
            <div className="card stat">
              <span className="stat__label">Orders</span>
              <span className="stat__value muted">—</span>
            </div>
            <div className="card stat">
              <span className="stat__label">Avg order</span>
              <span className="stat__value muted">—</span>
            </div>
          </section>

          <section className="card card--pad-lg">
            <div className="empty">
              <span className="empty__icon" aria-hidden="true" />
              <h2>No data yet</h2>
              <p>
                Connect your Swiggy or Zepto account and Overdulge will sync your order history,
                then break down where your money and calories are going.
              </p>
              <Link className="btn btn-primary" to="/settings">
                Link an account
              </Link>
            </div>
          </section>
        </main>
      </div>
    </>
  )
}
