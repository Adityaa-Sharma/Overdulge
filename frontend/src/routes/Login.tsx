import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { useSession } from '../lib/session'
import { requestEmailOtp, signInWithGoogle, verifyEmailOtp } from '../lib/supabase'

type Stage = 'request' | 'verify'
type Pending = 'otp-request' | 'otp-verify' | 'google' | null

export default function Login() {
  const { status } = useSession()
  const [stage, setStage] = useState<Stage>('request')
  const [email, setEmail] = useState('')
  const [otp, setOtp] = useState('')
  const [pending, setPending] = useState<Pending>(null)
  const [error, setError] = useState<string | null>(null)

  if (status === 'authenticated') {
    return <Navigate to="/" replace />
  }

  async function handleRequestOtp(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setPending('otp-request')
    try {
      await requestEmailOtp(email)
      setStage('verify')
    } catch {
      setError('Could not send the code. Check the email address and try again.')
    } finally {
      setPending(null)
    }
  }

  async function handleVerifyOtp(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setPending('otp-verify')
    try {
      await verifyEmailOtp(email, otp)
    } catch {
      setError('That code is incorrect or has expired. Request a new one.')
    } finally {
      setPending(null)
    }
  }

  async function handleGoogleSignIn() {
    setError(null)
    setPending('google')
    try {
      await signInWithGoogle()
    } catch {
      setError('Could not start Google sign-in. Please try again.')
      setPending(null)
    }
  }

  return (
    <div className="auth">
      <div className="auth__card card card--pad-lg">
        <div className="auth__brand">
          <span className="brand__mark" aria-hidden="true" />
          <h1>Welcome to Overdulge</h1>
          <p className="muted">Sign in to see where your food spend really goes.</p>
        </div>

      {error && <p role="alert">{error}</p>}

      {stage === 'request' && (
        <form onSubmit={handleRequestOtp}>
          <label htmlFor="email">Email address</label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={pending === 'otp-request'}
          />
          <button className="btn-primary btn-block" type="submit" disabled={pending === 'otp-request'}>
            {pending === 'otp-request' ? 'Sending code…' : 'Send code'}
          </button>
        </form>
      )}

      {stage === 'verify' && (
        <form onSubmit={handleVerifyOtp}>
          <p className="muted">
            We emailed a code to {email}.{' '}
            <button className="btn-ghost" type="button" onClick={() => setStage('request')}>
              Use a different email
            </button>
          </p>
          <label htmlFor="otp">6-digit code</label>
          <input
            id="otp"
            name="otp"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            required
            value={otp}
            onChange={(event) => setOtp(event.target.value)}
            disabled={pending === 'otp-verify'}
          />
          <button className="btn-primary btn-block" type="submit" disabled={pending === 'otp-verify'}>
            {pending === 'otp-verify' ? 'Verifying…' : 'Verify code'}
          </button>
        </form>
      )}

        <div className="divider">or</div>

        <button className="btn-block" type="button" onClick={handleGoogleSignIn} disabled={pending === 'google'}>
          {pending === 'google' ? 'Redirecting…' : 'Continue with Google'}
        </button>
      </div>
    </div>
  )
}
