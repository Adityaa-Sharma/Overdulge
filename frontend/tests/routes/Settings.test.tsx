import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Settings from '../../src/routes/Settings'

const { getLinkStatus, startLink, unlink, getSyncStatus, triggerSync, ApiError } = vi.hoisted(
  () => {
    class ApiError extends Error {
      status: number
      constructor(message: string, status: number) {
        super(message)
        this.name = 'ApiError'
        this.status = status
      }
    }
    return {
      getLinkStatus: vi.fn(),
      startLink: vi.fn(),
      unlink: vi.fn(),
      getSyncStatus: vi.fn(),
      triggerSync: vi.fn(),
      ApiError,
    }
  },
)

vi.mock('../../src/lib/api', () => ({
  getLinkStatus,
  startLink,
  unlink,
  getSyncStatus,
  triggerSync,
  ApiError,
}))

const NOT_LINKED = [
  { platform: 'swiggy', linked: false, linked_at: null },
  { platform: 'zepto', linked: false, linked_at: null },
]

const SWIGGY_LINKED = [
  { platform: 'swiggy', linked: true, linked_at: '2026-07-01T10:00:00Z' },
  { platform: 'zepto', linked: false, linked_at: null },
]

function renderSettings(initialEntry = '/settings') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Settings />
    </MemoryRouter>,
  )
}

const SYNC_EMPTY: never[] = []

const SYNC_LINKED = [
  {
    platform: 'swiggy_food',
    last_sync_at: '2026-07-20T09:00:00Z',
    orders_captured_last_run: 4,
    warning: null,
  },
  {
    platform: 'swiggy_instamart',
    last_sync_at: null,
    orders_captured_last_run: null,
    warning: "Instamart's order history is only available for roughly the last 15 days.",
  },
]

beforeEach(() => {
  vi.resetAllMocks()
  getSyncStatus.mockResolvedValue(SYNC_EMPTY)
  Object.defineProperty(window, 'location', {
    value: { href: '' },
    writable: true,
    configurable: true,
  })
})

describe('Settings', () => {
  it('shows a loading skeleton before link status resolves', () => {
    getLinkStatus.mockReturnValue(new Promise(() => {}))

    renderSettings()

    expect(screen.getByRole('heading', { name: /account settings/i })).toBeInTheDocument()
    expect(document.querySelector('.link-list__skeleton')).toBeInTheDocument()
  })

  it('renders link status from a mocked GET /links response', async () => {
    getLinkStatus.mockResolvedValue(SWIGGY_LINKED)

    renderSettings()

    await waitFor(() => expect(screen.getByText(/^not linked$/i)).toBeInTheDocument())
    expect(screen.getByText(/linked · last linked/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^unlink$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /link zepto/i })).toBeInTheDocument()
  })

  it('shows a retry option when link status fails to load, and recovers on retry', async () => {
    getLinkStatus.mockRejectedValueOnce(new Error('network error'))

    renderSettings()

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load link status/i),
    )

    getLinkStatus.mockResolvedValue(NOT_LINKED)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getAllByText(/^not linked$/i)).toHaveLength(2))
  })

  it('clicking "Link Swiggy" starts the flow and navigates the browser to the authorization url', async () => {
    getLinkStatus.mockResolvedValue(NOT_LINKED)
    startLink.mockResolvedValue({ authorization_url: 'https://mcp.swiggy.com/auth/authorize?x=1' })

    renderSettings()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /link swiggy/i })).toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole('button', { name: /link swiggy/i }))

    await waitFor(() => expect(startLink).toHaveBeenCalledWith('swiggy'))
    await waitFor(() =>
      expect(window.location.href).toBe('https://mcp.swiggy.com/auth/authorize?x=1'),
    )
  })

  it("shows the backend's reason when linking can't start, instead of telling the user to retry", async () => {
    // "Please try again" is worse than useless when the platform has refused
    // our callback domain — it sends the user round a loop that cannot work.
    getLinkStatus.mockResolvedValue(NOT_LINKED)
    startLink.mockRejectedValue(
      new ApiError("Zepto isn't accepting Overdulge's connection request right now.", 502),
    )

    renderSettings()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /link zepto/i })).toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole('button', { name: /link zepto/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/isn't accepting/i)
    expect(alert).not.toHaveTextContent(/please try again/i)
  })

  it('falls back to the generic message when the failure is not a 502', async () => {
    getLinkStatus.mockResolvedValue(NOT_LINKED)
    startLink.mockRejectedValue(new TypeError('network down'))

    renderSettings()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /link swiggy/i })).toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole('button', { name: /link swiggy/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /couldn't start linking swiggy\. please try again\./i,
    )
  })

  it('re-enables the Link button after a failure so the user is not stuck', async () => {
    getLinkStatus.mockResolvedValue(NOT_LINKED)
    startLink.mockRejectedValue(new ApiError('nope', 502))

    renderSettings()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /link zepto/i })).toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole('button', { name: /link zepto/i }))

    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: /link zepto/i })).toBeEnabled()
  })

  it('clicking "Unlink" deletes the link and updates the UI without a full reload', async () => {
    getLinkStatus.mockResolvedValue(SWIGGY_LINKED)
    unlink.mockResolvedValue(undefined)

    renderSettings()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^unlink$/i })).toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole('button', { name: /^unlink$/i }))

    await waitFor(() => expect(unlink).toHaveBeenCalledWith('swiggy'))
    await waitFor(() => expect(screen.getAllByText(/^not linked$/i)).toHaveLength(2))
    expect(screen.getByRole('button', { name: /link swiggy/i })).toBeInTheDocument()
  })

  it('shows a success toast, refetches link status, and strips the query param after a successful link', async () => {
    getLinkStatus.mockResolvedValueOnce(NOT_LINKED).mockResolvedValueOnce(SWIGGY_LINKED)

    renderSettings('/settings?linked=swiggy&status=ok')

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/swiggy linked successfully/i),
    )
    await waitFor(() => expect(getLinkStatus).toHaveBeenCalledTimes(2))
  })

  it('shows an error toast when the return leg reports a failure', async () => {
    getLinkStatus.mockResolvedValue(NOT_LINKED)

    renderSettings('/settings?linked=zepto&status=error')

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/couldn't link zepto/i),
    )
  })
})

describe('Sync status', () => {
  it('shows an empty state when no platform has ever been linked', async () => {
    getLinkStatus.mockResolvedValue(NOT_LINKED)
    getSyncStatus.mockResolvedValue(SYNC_EMPTY)

    renderSettings()

    await waitFor(() =>
      expect(screen.getByText(/link an account above to start syncing/i)).toBeInTheDocument(),
    )
  })

  it('renders last sync time, orders captured, and the Instamart warning only for that platform', async () => {
    getLinkStatus.mockResolvedValue(NOT_LINKED)
    getSyncStatus.mockResolvedValue(SYNC_LINKED)

    renderSettings()

    await waitFor(() => expect(screen.getByText(/4 orders captured/i)).toBeInTheDocument())
    expect(screen.getByText(/never synced/i)).toBeInTheDocument()
    expect(screen.getByText(/15 days/i)).toBeInTheDocument()
    const foodItem = screen.getByText('Swiggy Food').closest('li')!
    expect(within(foodItem).queryByText(/15 days/i)).not.toBeInTheDocument()
  })

  it('disables "Sync now" immediately on click and re-enables once the call resolves', async () => {
    getLinkStatus.mockResolvedValue(NOT_LINKED)
    getSyncStatus.mockResolvedValue(SYNC_LINKED)
    let resolveTrigger: () => void = () => {}
    triggerSync.mockReturnValue(
      new Promise<void>((resolve) => {
        resolveTrigger = resolve
      }),
    )

    renderSettings()
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /^sync now$/i })).toHaveLength(2),
    )

    const [syncButton] = screen.getAllByRole('button', { name: /^sync now$/i })
    fireEvent.click(syncButton)

    expect(syncButton).toBeDisabled()

    resolveTrigger()
    await waitFor(() => expect(syncButton).not.toBeDisabled())
  })

  it('shows a non-error "already syncing" state on a 409, not a failure toast', async () => {
    getLinkStatus.mockResolvedValue(NOT_LINKED)
    getSyncStatus.mockResolvedValue(SYNC_LINKED)
    triggerSync.mockRejectedValue(new ApiError('Sync already in progress', 409))

    renderSettings()
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /^sync now$/i })).toHaveLength(2),
    )

    const [syncButton] = screen.getAllByRole('button', { name: /^sync now$/i })
    fireEvent.click(syncButton)

    await waitFor(() => expect(screen.getByText(/already in progress/i)).toBeInTheDocument())
    expect(screen.queryByRole('alert', { name: /couldn't sync/i })).not.toBeInTheDocument()
    expect(screen.getByText(/already in progress/i)).toHaveAttribute('role', 'status')
  })

  it('shows a failure state and re-enables the button on a non-409 error', async () => {
    getLinkStatus.mockResolvedValue(NOT_LINKED)
    getSyncStatus.mockResolvedValue(SYNC_LINKED)
    triggerSync.mockRejectedValue(new ApiError('boom', 500))

    renderSettings()
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /^sync now$/i })).toHaveLength(2),
    )

    const [syncButton] = screen.getAllByRole('button', { name: /^sync now$/i })
    fireEvent.click(syncButton)

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/couldn't sync/i))
    expect(syncButton).not.toBeDisabled()
  })
})
