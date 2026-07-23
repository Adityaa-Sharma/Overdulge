import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Login from '../../src/routes/Login'

const { useSession } = vi.hoisted(() => ({ useSession: vi.fn() }))
const { requestEmailOtp, verifyEmailOtp, signInWithGoogle } = vi.hoisted(() => ({
  requestEmailOtp: vi.fn(),
  verifyEmailOtp: vi.fn(),
  signInWithGoogle: vi.fn(),
}))

vi.mock('../../src/lib/session', () => ({ useSession }))
vi.mock('../../src/lib/supabase', () => ({ requestEmailOtp, verifyEmailOtp, signInWithGoogle }))

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
    expect(await screen.findByLabelText(/6-digit code/i)).toBeInTheDocument()
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

  it('verifies the entered code', async () => {
    useSession.mockReturnValue({ status: 'anonymous' })
    requestEmailOtp.mockResolvedValue(undefined)
    verifyEmailOtp.mockResolvedValue(undefined)

    renderLogin()

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'friend@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send code/i }))
    await screen.findByLabelText(/6-digit code/i)

    fireEvent.change(screen.getByLabelText(/6-digit code/i), { target: { value: '123456' } })
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
    await screen.findByLabelText(/6-digit code/i)

    fireEvent.change(screen.getByLabelText(/6-digit code/i), { target: { value: '000000' } })
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
