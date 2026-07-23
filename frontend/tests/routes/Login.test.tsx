import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Login from '../../src/routes/Login'

const { useSession } = vi.hoisted(() => ({ useSession: vi.fn() }))
const { requestEmailOtp, verifyEmailOtp, signInWithGoogle, isEmailRateLimitError } = vi.hoisted(
  () => ({
    requestEmailOtp: vi.fn(),
    verifyEmailOtp: vi.fn(),
    signInWithGoogle: vi.fn(),
    // The real predicate — the point of these tests is the copy it selects.
    isEmailRateLimitError: (error: unknown) =>
      typeof error === 'object' &&
      error !== null &&
      ((error as { code?: unknown }).code === 'over_email_send_rate_limit' ||
        (error as { status?: unknown }).status === 429),
  }),
)

vi.mock('../../src/lib/session', () => ({ useSession }))
vi.mock('../../src/lib/supabase', () => ({
  requestEmailOtp,
  verifyEmailOtp,
  signInWithGoogle,
  isEmailRateLimitError,
}))

function renderLogin() {
  render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<h1>Home</h1>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Login', () => {
  it('redirects an already-authenticated user to /', async () => {
    useSession.mockReturnValue({ status: 'authenticated' })

    renderLogin()

    await waitFor(() => expect(screen.getByRole('heading', { name: /home/i })).toBeInTheDocument())
  })

  it('requests an OTP for the entered email and advances to the verify stage', async () => {
    useSession.mockReturnValue({ status: 'anonymous' })
    requestEmailOtp.mockResolvedValue(undefined)

    renderLogin()

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'friend@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send code/i }))

    await waitFor(() => expect(requestEmailOtp).toHaveBeenCalledWith('friend@example.com'))
    expect(await screen.findByLabelText(/code from your email/i)).toBeInTheDocument()
  })

  it('shows a retryable error when requesting the OTP fails', async () => {
    useSession.mockReturnValue({ status: 'anonymous' })
    requestEmailOtp.mockRejectedValue(new Error('network down'))

    renderLogin()

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'friend@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send code/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not send the code/i)
    expect(screen.getByLabelText(/email address/i)).toBeInTheDocument()
  })

  it('does not blame the email address when the send quota is exhausted', async () => {
    // Supabase's built-in mailer answers 429 over_email_send_rate_limit once
    // the project's hourly allowance is gone. The address is fine; telling the
    // user to check it sends them off correcting something that isn't wrong.
    useSession.mockReturnValue({ status: 'anonymous' })
    requestEmailOtp.mockRejectedValue(
      Object.assign(new Error('email rate limit exceeded'), {
        code: 'over_email_send_rate_limit',
        status: 429,
      }),
    )

    renderLogin()

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'friend@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send code/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/too many codes requested/i)
    expect(alert).not.toHaveTextContent(/check the email address/i)
  })

  it('points at Google sign-in as a way through a rate limit', async () => {
    useSession.mockReturnValue({ status: 'anonymous' })
    requestEmailOtp.mockRejectedValue(Object.assign(new Error('rate limited'), { status: 429 }))

    renderLogin()

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'friend@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send code/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/google/i)
  })

  it('keeps the user on the request stage when the code was never sent', async () => {
    useSession.mockReturnValue({ status: 'anonymous' })
    requestEmailOtp.mockRejectedValue(Object.assign(new Error('rate limited'), { status: 429 }))

    renderLogin()

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'friend@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send code/i }))

    await screen.findByRole('alert')
    // Advancing to "enter your code" would strand the user waiting for an
    // email that was never sent.
    expect(screen.queryByLabelText(/code from your email/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/email address/i)).toBeInTheDocument()
  })

  it('accepts a code longer than six digits', async () => {
    // The project currently issues 8-digit codes; nothing in the form may
    // assume otherwise.
    useSession.mockReturnValue({ status: 'anonymous' })
    requestEmailOtp.mockResolvedValue(undefined)
    verifyEmailOtp.mockResolvedValue(undefined)

    renderLogin()

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'friend@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send code/i }))
    await screen.findByLabelText(/code from your email/i)

    fireEvent.change(screen.getByLabelText(/code from your email/i), {
      target: { value: '17004469' },
    })
    fireEvent.click(screen.getByRole('button', { name: /verify code/i }))

    await waitFor(() =>
      expect(verifyEmailOtp).toHaveBeenCalledWith('friend@example.com', '17004469'),
    )
  })

  it('verifies the entered code', async () => {
    useSession.mockReturnValue({ status: 'anonymous' })
    requestEmailOtp.mockResolvedValue(undefined)
    verifyEmailOtp.mockResolvedValue(undefined)

    renderLogin()

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'friend@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send code/i }))
    await screen.findByLabelText(/code from your email/i)

    fireEvent.change(screen.getByLabelText(/code from your email/i), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: /verify code/i }))

    await waitFor(() =>
      expect(verifyEmailOtp).toHaveBeenCalledWith('friend@example.com', '123456'),
    )
  })

  it('shows a retryable error when the code is wrong', async () => {
    useSession.mockReturnValue({ status: 'anonymous' })
    requestEmailOtp.mockResolvedValue(undefined)
    verifyEmailOtp.mockRejectedValue(new Error('invalid token'))

    renderLogin()

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'friend@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send code/i }))
    await screen.findByLabelText(/code from your email/i)

    fireEvent.change(screen.getByLabelText(/code from your email/i), { target: { value: '000000' } })
    fireEvent.click(screen.getByRole('button', { name: /verify code/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/incorrect or has expired/i)
  })

  it('starts the Google sign-in flow', async () => {
    useSession.mockReturnValue({ status: 'anonymous' })
    signInWithGoogle.mockResolvedValue(undefined)

    renderLogin()

    fireEvent.click(screen.getByRole('button', { name: /continue with google/i }))

    await waitFor(() => expect(signInWithGoogle).toHaveBeenCalledTimes(1))
  })
})
