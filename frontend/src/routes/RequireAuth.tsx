import { Navigate, Outlet } from 'react-router'
import { useSession } from '../lib/session'

/** Guard for authenticated routes: redirects to /login until a session resolves. */
export default function RequireAuth() {
  const { status } = useSession()

  if (status === 'checking') {
    return <p role="status">Checking session…</p>
  }

  if (status === 'anonymous') {
    return <Navigate to="/login" replace />
  }

  return <Outlet />
}
