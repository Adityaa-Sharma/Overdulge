import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import Login from '../../src/routes/Login'

const { useSession } = vi.hoisted(() => ({ useSession: vi.fn() }))
const { signInWithGoogle } = vi.hoisted(() => ({ signInWithGoogle: vi.fn() }))

vi.mock('../../src/lib/session', () => ({ useSession }))
vi.mock('../../src/lib/supabase', () => ({ signInWithGoogle }))

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

  it('offers Google as the only sign-in method', () => {
    // Email OTP was removed (no SMTP on the project). Guards against the email
    // form quietly reappearing.
    useSession.mockReturnValue({ status: 'anonymous' })

    renderLogin()

    expect(screen.getByRole('button', { name: /continue with google/i })).toBeInTheDocument()
    expect(screen.queryByLabelText(/email/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /send code/i })).not.toBeInTheDocument()
  })

  it('starts the Google sign-in flow', async () => {
    useSession.mockReturnValue({ status: 'anonymous' })
    signInWithGoogle.mockResolvedValue(undefined)

    renderLogin()

    fireEvent.click(screen.getByRole('button', { name: /continue with google/i }))

    await waitFor(() => expect(signInWithGoogle).toHaveBeenCalledTimes(1))
  })

  it('shows a retryable error when Google sign-in fails to start', async () => {
    useSession.mockReturnValue({ status: 'anonymous' })
    signInWithGoogle.mockRejectedValue(new Error('popup blocked'))

    renderLogin()

    fireEvent.click(screen.getByRole('button', { name: /continue with google/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not start google sign-in/i)
    // The button must return to a usable state so the user can retry.
    expect(screen.getByRole('button', { name: /continue with google/i })).toBeEnabled()
  })
})
