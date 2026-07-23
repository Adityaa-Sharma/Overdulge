import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  getDashboard,
  getLinkStatus,
  getSyncStatus,
  startLink,
  triggerSync,
  unlink,
} from '../../src/lib/api'

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

/**
 * These assert the *whole* path, not a fragment of it. The production outage
 * that 404'd every authenticated call shipped past a suite that only checked
 * `toContain('/links')` — which passes just as happily against a URL missing
 * the /api/v1 prefix entirely. Anchoring to the end is what makes them useful.
 */
describe('request URLs', () => {
  it.each([
    ['getLinkStatus', () => getLinkStatus(), /\/api\/v1\/links$/],
    ['startLink', () => startLink('swiggy'), /\/api\/v1\/links\/swiggy\/start$/],
    ['unlink', () => unlink('zepto'), /\/api\/v1\/links\/zepto$/],
    ['getSyncStatus', () => getSyncStatus(), /\/api\/v1\/sync\/status$/],
    ['triggerSync', () => triggerSync('zepto'), /\/api\/v1\/sync\/zepto$/],
    ['getDashboard', () => getDashboard(), /\/api\/v1\/dashboard$/],
  ])('%s targets the versioned API path', async (_name, call, expected) => {
    const fetchMock = mockFetchOnce({ json: async () => ({}) })

    await call()

    expect(fetchMock.mock.calls[0][0]).toMatch(expected)
  })

  it('never doubles the /api/v1 prefix', async () => {
    const fetchMock = mockFetchOnce({ json: async () => ({}) })

    await getLinkStatus()

    expect(fetchMock.mock.calls[0][0]).not.toContain('/api/v1/api/v1')
  })
})

describe('startLink failure messages', () => {
  it("surfaces the backend's explanation so the UI can show why linking failed", async () => {
    mockFetchOnce({
      ok: false,
      status: 502,
      json: async () => ({ detail: "Zepto isn't accepting Overdulge's connection request." }),
    })

    await expect(startLink('zepto')).rejects.toThrow(/isn't accepting/)
  })

  it('falls back to a generic message when the body is not JSON', async () => {
    mockFetchOnce({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError('Unexpected token <')
      },
    })

    await expect(startLink('swiggy')).rejects.toThrow('Failed to start link')
  })

  it('keeps the status code so callers can distinguish 502 from other failures', async () => {
    mockFetchOnce({ ok: false, status: 502, json: async () => ({ detail: 'nope' }) })

    await expect(startLink('swiggy')).rejects.toMatchObject({ status: 502 })
  })
})
