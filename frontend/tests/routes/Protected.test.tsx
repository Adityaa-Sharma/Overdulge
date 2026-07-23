import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import Protected from '../../src/routes/Protected'

const { useSession } = vi.hoisted(() => ({ useSession: vi.fn() }))

vi.mock('../../src/lib/session', () => ({ useSession }))

describe('Protected placeholder page', () => {
  it('renders the signed-in placeholder', () => {
    useSession.mockReturnValue({ logout: vi.fn() })

    render(<Protected />)

    expect(screen.getByRole('heading', { name: /overdulge/i })).toBeInTheDocument()
  })

  it('calls logout when the log out button is clicked', async () => {
    const logout = vi.fn().mockResolvedValue(undefined)
    useSession.mockReturnValue({ logout })

    render(<Protected />)
    fireEvent.click(screen.getByRole('button', { name: /log out/i }))

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1))
  })
})
