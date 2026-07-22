import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Login from '../../src/routes/Login'
import Protected from '../../src/routes/Protected'

const { getSession } = vi.hoisted(() => ({ getSession: vi.fn() }))

vi.mock('../../src/lib/supabase', () => ({ getSession }))

function renderAtRoot() {
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Protected />} />
        <Route path="/login" element={<Login />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Protected route guard', () => {
  it('redirects to /login when there is no session', async () => {
    getSession.mockResolvedValue(null)

    renderAtRoot()

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /log in/i })).toBeInTheDocument(),
    )
  })

  it('renders the protected placeholder when a session exists', async () => {
    getSession.mockResolvedValue({ access_token: 'token' })

    renderAtRoot()

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /overdulge/i })).toBeInTheDocument(),
    )
  })
})
