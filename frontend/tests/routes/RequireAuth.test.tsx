import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import RequireAuth from '../../src/routes/RequireAuth'

const { useSession } = vi.hoisted(() => ({ useSession: vi.fn() }))

vi.mock('../../src/lib/session', () => ({ useSession }))

function renderGuard() {
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/login" element={<h1>Log in</h1>} />
        <Route element={<RequireAuth />}>
          <Route path="/" element={<h1>Protected content</h1>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('RequireAuth', () => {
  it('shows a checking indicator while the session is resolving', () => {
    useSession.mockReturnValue({ status: 'checking' })

    renderGuard()

    expect(screen.getByRole('status')).toHaveTextContent(/checking session/i)
  })

  it('redirects to /login when there is no session', async () => {
    useSession.mockReturnValue({ status: 'anonymous' })

    renderGuard()

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /log in/i })).toBeInTheDocument(),
    )
  })

  it('renders the protected route content when authenticated', async () => {
    useSession.mockReturnValue({ status: 'authenticated' })

    renderGuard()

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /protected content/i })).toBeInTheDocument(),
    )
  })
})
