import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Settings from '../../src/routes/Settings'

const { getLinkStatus, startLink, unlink } = vi.hoisted(() => ({
  getLinkStatus: vi.fn(),
  startLink: vi.fn(),
  unlink: vi.fn(),
}))

vi.mock('../../src/lib/api', () => ({ getLinkStatus, startLink, unlink }))

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

beforeEach(() => {
  vi.resetAllMocks()
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
