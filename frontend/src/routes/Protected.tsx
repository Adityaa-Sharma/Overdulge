import { useState } from 'react'
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
    <main>
      <h1>Overdulge</h1>
      <p>You're signed in. The dashboard is coming soon.</p>
      <button type="button" onClick={handleLogout} disabled={loggingOut}>
        {loggingOut ? 'Logging out…' : 'Log out'}
      </button>
    </main>
  )
}
