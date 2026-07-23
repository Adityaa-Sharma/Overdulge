import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SessionProvider, useSession } from '../../src/lib/session'

const { getSession, onAuthStateChange, signOut } = vi.hoisted(() => ({
  getSession: vi.fn(),
  onAuthStateChange: vi.fn(),
  signOut: vi.fn(),
}))

vi.mock('../../src/lib/supabase', () => ({ getSession, onAuthStateChange, signOut }))

function Probe() {
  const { status, session, logout } = useSession()
  return (
    <div>
      <p>status:{status}</p>
      <p>token:{session?.access_token ?? 'none'}</p>
      <button type="button" onClick={() => logout()}>
        logout
      </button>
    </div>
  )
}

describe('SessionProvider / useSession', () => {
  it('starts checking, then resolves to anonymous when there is no stored session', async () => {
    getSession.mockResolvedValue(null)
    onAuthStateChange.mockReturnValue(() => {})

    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    )

    await waitFor(() => expect(screen.getByText('status:anonymous')).toBeInTheDocument())
  })

  it('resolves to authenticated when a session is already persisted in storage', async () => {
    getSession.mockResolvedValue({ access_token: 'stored-token' })
    onAuthStateChange.mockReturnValue(() => {})

    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    )

    await waitFor(() => expect(screen.getByText('status:authenticated')).toBeInTheDocument())
    expect(screen.getByText('token:stored-token')).toBeInTheDocument()
  })

  it('picks the session back up on a simulated reload (fresh mount, same storage)', async () => {
    getSession.mockResolvedValue({ access_token: 'stored-token' })
    onAuthStateChange.mockReturnValue(() => {})

    const { unmount } = render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    )
    await waitFor(() => expect(screen.getByText('status:authenticated')).toBeInTheDocument())

    unmount()

    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    )
    await waitFor(() => expect(screen.getByText('status:authenticated')).toBeInTheDocument())
  })

  it('updates status when the underlying auth state changes', async () => {
    getSession.mockResolvedValue(null)
    let emit: (session: { access_token: string } | null) => void = () => {}
    onAuthStateChange.mockImplementation((callback) => {
      emit = callback
      return () => {}
    })

    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    )
    await waitFor(() => expect(screen.getByText('status:anonymous')).toBeInTheDocument())

    act(() => emit({ access_token: 'fresh-token' }))

    await waitFor(() => expect(screen.getByText('status:authenticated')).toBeInTheDocument())
  })

  it('logout calls through to supabase signOut', async () => {
    getSession.mockResolvedValue({ access_token: 'stored-token' })
    onAuthStateChange.mockReturnValue(() => {})
    signOut.mockResolvedValue(undefined)

    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    )
    await waitFor(() => expect(screen.getByText('status:authenticated')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: 'logout' }))

    await waitFor(() => expect(signOut).toHaveBeenCalledTimes(1))
  })

  it('throws when useSession is called outside a SessionProvider', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<Probe />)).toThrow('useSession must be used within a SessionProvider')
    consoleError.mockRestore()
  })
})
