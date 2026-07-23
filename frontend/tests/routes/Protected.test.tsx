import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Protected from '../../src/routes/Protected'

const { useSession } = vi.hoisted(() => ({ useSession: vi.fn() }))

vi.mock('../../src/lib/session', () => ({ useSession }))

// Protected uses <Link>, so it must render inside a router.
function renderProtected() {
  return render(
    <MemoryRouter>
      <Protected />
    </MemoryRouter>,
  )
}

describe('Protected dashboard shell', () => {
  it('renders the dashboard shell with the brand and a heading', () => {
    useSession.mockReturnValue({ logout: vi.fn() })

    renderProtected()

    expect(screen.getByRole('heading', { name: /dashboard/i })).toBeInTheDocument()
    // Exact match targets the brand wordmark, not the sentence in the empty state.
    expect(screen.getByText('Overdulge')).toBeInTheDocument()
  })

  it('prompts the user to link an account when there is no data', () => {
    useSession.mockReturnValue({ logout: vi.fn() })

    renderProtected()

    expect(screen.getByRole('link', { name: /link an account/i })).toBeInTheDocument()
  })

  it('calls logout when the log out button is clicked', async () => {
    const logout = vi.fn().mockResolvedValue(undefined)
    useSession.mockReturnValue({ logout })

    renderProtected()
    fireEvent.click(screen.getByRole('button', { name: /log out/i }))

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1))
  })
})
