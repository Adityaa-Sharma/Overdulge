import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  askQuery,
  deleteBudget,
  getBudgetSuggestions,
  getBudgets,
  getDashboard,
  getLinkStatus,
  getSyncStatus,
  startLink,
  triggerSync,
  unlink,
  upsertBudget,
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

describe('askQuery', () => {
  it('posts the question and returns the parsed answer', async () => {
    const body = { amount_paise: 12300, explanation: 'You spent ₹123.', has_data: true }
    const fetchMock = mockFetchOnce({ json: async () => body })

    const result = await askQuery('how much did I spend on milk?')

    expect(result).toEqual(body)
    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ question: 'how much did I spend on milk?' })
  })

  it("surfaces the backend's error envelope message on failure", async () => {
    mockFetchOnce({
      ok: false,
      status: 502,
      json: async () => ({ error: { code: 'agent_error', message: 'Something went wrong.' } }),
    })

    await expect(askQuery('how much?')).rejects.toThrow('Something went wrong.')
  })

  it('falls back to a generic message when the body has no error envelope', async () => {
    mockFetchOnce({
      ok: false,
      status: 504,
      json: async () => {
        throw new SyntaxError('Unexpected token <')
      },
    })

    await expect(askQuery('how much?')).rejects.toThrow(/something went wrong answering/i)
  })
})

describe('getBudgets', () => {
  it('returns the parsed budgets response and attaches the bearer token', async () => {
    const body = { month: '2026-07-01', budgets: [] }
    const fetchMock = mockFetchOnce({ json: async () => body })

    const result = await getBudgets('2026-07-01')

    expect(result).toEqual(body)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/budgets?month=2026-07-01')
    expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token')
  })

  it('throws ApiError when the response is not ok', async () => {
    mockFetchOnce({ ok: false, status: 500 })

    await expect(getBudgets('2026-07-01')).rejects.toThrow(ApiError)
  })
})

describe('upsertBudget', () => {
  it('posts the cap and returns the saved row', async () => {
    const body = { id: 'b-1', user_id: 'u-1', month: '2026-07-01', category: null, cap_paise: 800000 }
    const fetchMock = mockFetchOnce({ status: 201, json: async () => body })

    const result = await upsertBudget({ month: '2026-07-01', category: null, cap_paise: 800000 })

    expect(result).toEqual(body)
    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({
      month: '2026-07-01',
      category: null,
      cap_paise: 800000,
    })
  })

  it("surfaces the backend's detail message on failure", async () => {
    mockFetchOnce({
      ok: false,
      status: 422,
      json: async () => ({ detail: 'invalid month' }),
    })

    await expect(
      upsertBudget({ month: 'bad', category: null, cap_paise: 100 }),
    ).rejects.toThrow('invalid month')
  })
})

describe('deleteBudget', () => {
  it('sends a DELETE request for the budget id', async () => {
    const fetchMock = mockFetchOnce({ status: 204, json: async () => undefined })

    await deleteBudget('b-1')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/budgets/b-1')
    expect(init.method).toBe('DELETE')
  })

  it('throws ApiError on failure', async () => {
    mockFetchOnce({ ok: false, status: 404 })

    await expect(deleteBudget('missing')).rejects.toThrow(ApiError)
  })
})

describe('getBudgetSuggestions', () => {
  it('returns the parsed suggestions list', async () => {
    const body = { suggestions: [{ category: 'dining', text: 'Cut back on dining.' }] }
    const fetchMock = mockFetchOnce({ json: async () => body })

    const result = await getBudgetSuggestions('2026-07-01')

    expect(result).toEqual(body)
    expect(fetchMock.mock.calls[0][0]).toContain('/budgets/suggestions?month=2026-07-01')
  })

  it('throws ApiError when the response is not ok', async () => {
    mockFetchOnce({ ok: false, status: 500 })

    await expect(getBudgetSuggestions('2026-07-01')).rejects.toThrow(ApiError)
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
    ['askQuery', () => askQuery('how much did I spend?'), /\/api\/v1\/query$/],
    ['getBudgets', () => getBudgets('2026-07-01'), /\/api\/v1\/budgets\?month=2026-07-01$/],
    [
      'upsertBudget',
      () => upsertBudget({ month: '2026-07-01', category: null, cap_paise: 100 }),
      /\/api\/v1\/budgets$/,
    ],
    ['deleteBudget', () => deleteBudget('b-1'), /\/api\/v1\/budgets\/b-1$/],
    [
      'getBudgetSuggestions',
      () => getBudgetSuggestions('2026-07-01'),
      /\/api\/v1\/budgets\/suggestions\?month=2026-07-01$/,
    ],
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
