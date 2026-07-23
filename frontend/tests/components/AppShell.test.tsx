import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import AppShell from '../../src/components/AppShell'

const { useSession } = vi.hoisted(() => ({ useSession: vi.fn() }))

vi.mock('../../src/lib/session', () => ({ useSession }))

function renderShell(initialEntry = '/') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<h1>Dashboard</h1>} />
          <Route path="/settings" element={<h1>Account settings</h1>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('AppShell', () => {
  it('renders the brand and the routed page beneath it', () => {
    useSession.mockReturnValue({ logout: vi.fn() })

    renderShell()

    expect(screen.getByText('Overdulge')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /dashboard/i })).toBeInTheDocument()
  })

  // Settings used to render a bare <main> with no header, stranding the user.
  it('keeps the nav available on every authenticated screen, including settings', () => {
    useSession.mockReturnValue({ logout: vi.fn() })

    renderShell('/settings')

    expect(screen.getByRole('heading', { name: /account settings/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /dashboard/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /accounts/i })).toBeInTheDocument()
  })

  it('marks the current screen for assistive tech', () => {
    useSession.mockReturnValue({ logout: vi.fn() })

    renderShell('/settings')

    expect(screen.getByRole('link', { name: /accounts/i })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('calls logout when the log out button is clicked', async () => {
    const logout = vi.fn().mockResolvedValue(undefined)
    useSession.mockReturnValue({ logout })

    renderShell()
    fireEvent.click(screen.getByRole('button', { name: /log out/i }))

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1))
  })
})
