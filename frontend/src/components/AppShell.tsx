import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useSession } from '../lib/session'

/**
 * Chrome shared by every authenticated screen.
 *
 * This lives at the route level (see App.tsx) rather than inside each page so
 * that Settings can't become a nav dead-end again — previously it rendered a
 * bare <main> with no header, stranding anyone who navigated there.
 */
export default function AppShell() {
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
          <span className="brand__mark" aria-hidden="true">
            <span>O</span>
          </span>
          Overdulge
        </span>
        <nav className="app-nav" aria-label="Main">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/settings">Accounts</NavLink>
          <button className="btn-ghost" type="button" onClick={handleLogout} disabled={loggingOut}>
            {loggingOut ? 'Logging out…' : 'Log out'}
          </button>
        </nav>
      </header>

      <div className="container">
        <Outlet />
      </div>
    </>
  )
}
