import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { getSession } from '../lib/supabase'

type SessionStatus = 'checking' | 'authenticated' | 'anonymous'

export default function Protected() {
  const [status, setStatus] = useState<SessionStatus>('checking')

  useEffect(() => {
    let cancelled = false
    getSession().then((session) => {
      if (!cancelled) setStatus(session ? 'authenticated' : 'anonymous')
    })
    return () => {
      cancelled = true
    }
  }, [])

  if (status === 'checking') {
    return <p role="status">Checking session…</p>
  }

  if (status === 'anonymous') {
    return <Navigate to="/login" replace />
  }

  return (
    <main>
      <h1>Overdulge</h1>
      <p>You're signed in. The dashboard is coming soon.</p>
    </main>
  )
}
