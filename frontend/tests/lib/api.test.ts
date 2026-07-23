import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, getLinkStatus, startLink, unlink } from '../../src/lib/api'

const { getSession } = vi.hoisted(() => ({ getSession: vi.fn() }))

vi.mock('../../src/lib/supabase', () => ({ getSession }))

const originalFetch = global.fetch

function mockFetchOnce(response: { ok?: boolean; status?: number; json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => undefined,
    ...response,
  })
  global.fetch = fetchMock as unknown as typeof fetch
  return fetchMock
}

beforeEach(() => {
  getSession.mockResolvedValue({ access_token: 'test-token' })
})

afterEach(() => {
  global.fetch = originalFetch
  vi.restoreAllMocks()
})

describe('getLinkStatus', () => {
  it('returns the parsed link status list and attaches the bearer token', async () => {
    const body = [{ platform: 'swiggy', linked: false, linked_at: null }]
    const fetchMock = mockFetchOnce({ json: async () => body })

    const result = await getLinkStatus()

    expect(result).toEqual(body)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/links')
    expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token')
  })

  it('throws ApiError when the response is not ok', async () => {
    mockFetchOnce({ ok: false, status: 500 })

    await expect(getLinkStatus()).rejects.toThrow(ApiError)
  })
})

describe('startLink', () => {
  it('posts to /links/{platform}/start and returns the authorization url', async () => {
    const fetchMock = mockFetchOnce({ json: async () => ({ authorization_url: 'https://x' }) })

    const result = await startLink('swiggy')

    expect(result).toEqual({ authorization_url: 'https://x' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/links/swiggy/start')
    expect(init.method).toBe('POST')
  })

  it('throws ApiError when the response is not ok', async () => {
    mockFetchOnce({ ok: false, status: 500 })

    await expect(startLink('zepto')).rejects.toThrow(ApiError)
  })
})

describe('unlink', () => {
  it('sends a DELETE request for the platform', async () => {
    const fetchMock = mockFetchOnce({ status: 204, json: async () => undefined })

    await unlink('zepto')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/links/zepto')
    expect(init.method).toBe('DELETE')
  })

  it('throws ApiError on failure', async () => {
    mockFetchOnce({ ok: false, status: 404 })

    await expect(unlink('swiggy')).rejects.toThrow(ApiError)
  })
})
