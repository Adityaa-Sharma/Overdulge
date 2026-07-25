import { useState } from 'react'
import { Navigate } from 'react-router'
import { useSession } from '../lib/session'
import { signInWithGoogle } from '../lib/supabase'

export default function Login() {
  const { status } = useSession()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (status === 'authenticated') {
    return <Navigate to="/" replace />
  }

  async function handleGoogleSignIn() {
    setError(null)
    setPending(true)
    try {
      await signInWithGoogle()
    } catch {
      setError('Could not start Google sign-in. Please try again.')
      setPending(false)
    }
  }

  return (
    <div className="auth">
      <div className="auth__card card card--pad-lg">
        <div className="auth__brand">
          <span className="brand__mark" aria-hidden="true">
            <span>O</span>
          </span>
          <h1>
            Your cravings, <span className="hl">itemised</span>
          </h1>
          <p className="muted">
            Overdulge reads your Swiggy and Zepto history and shows you the tasty truth about
            where the money went.
          </p>
        </div>

        {error && <p role="alert">{error}</p>}

        <button
          className="btn-primary btn-block"
          type="button"
          onClick={handleGoogleSignIn}
          disabled={pending}
        >
          {pending ? 'Redirecting…' : 'Continue with Google'}
        </button>

        <p className="auth__trust">
          Read-only by design. We never place orders, and never sell your data.
        </p>
      </div>
    </div>
  )
}
