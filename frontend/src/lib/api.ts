import { getSession } from './supabase'

/**
 * The deploy secret holds the backend *origin* (e.g. https://…run.app), but the
 * API is served under the versioned /api/v1 prefix. Concatenating the origin
 * with paths like "/links" produced 404s on every authenticated call in
 * production while login (which talks to Supabase directly) kept working.
 *
 * Normalising here tolerates every reasonable form of the value — bare origin,
 * trailing slash, or one already ending in /api/v1 — so the frontend cannot be
 * broken again by how the secret happens to be written.
 */
const API_BASE_URL = `${(import.meta.env.VITE_API_BASE_URL ?? '')
  .replace(/\/+$/, '')
  .replace(/\/api\/v1$/, '')}/api/v1`

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * Thin wrapper around fetch for backend calls. Attaches the current Supabase
 * session JWT as a Bearer token and redirects to /login on a 401 response
 * (BRD AC-8: unauthenticated users can't reach protected data).
 */
export async function apiFetch(path: string, opts: RequestInit = {}): Promise<Response> {
  const session = await getSession()
  const headers = new Headers(opts.headers)
  if (session) {
    headers.set('Authorization', `Bearer ${session.access_token}`)
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...opts, headers })

  if (response.status === 401) {
    // The app is served from a sub-path on Pages (/Overdulge/), so a bare
    // "/login" lands on a 404 instead of the sign-in screen.
    window.location.assign(`${import.meta.env.BASE_URL}login`)
    throw new ApiError('Unauthorized', 401)
  }

  return response
}

/**
 * FastAPI puts human-readable failure text in `detail`. The link routes use it
 * to explain *why* linking could not start — "retrying won't help" reads very
 * differently from "try again" — so prefer it over a generic message whenever
 * the body actually has that shape.
 */
async function detailOr(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json()
    return typeof body?.detail === 'string' && body.detail.trim() ? body.detail : fallback
  } catch {
    return fallback
  }
}

export type LinkPlatform = 'swiggy' | 'zepto'

export interface LinkStatus {
  platform: LinkPlatform
  linked: boolean
  linked_at: string | null
}

/** Link status for every supported platform (FR-1.3). */
export async function getLinkStatus(): Promise<LinkStatus[]> {
  const response = await apiFetch('/links')
  if (!response.ok) {
    throw new ApiError('Failed to load link status', response.status)
  }
  return response.json()
}

/** Starts the OAuth 2.1 + PKCE + DCR flow for `platform`; returns the authorization URL to redirect the browser to. */
export async function startLink(platform: LinkPlatform): Promise<{ authorization_url: string }> {
  const response = await apiFetch(`/links/${platform}/start`, { method: 'POST' })
  if (!response.ok) {
    throw new ApiError(await detailOr(response, 'Failed to start link'), response.status)
  }
  return response.json()
}

/** Deletes the stored tokens for `platform` (FR-1.3). */
export async function unlink(platform: LinkPlatform): Promise<void> {
  const response = await apiFetch(`/links/${platform}`, { method: 'DELETE' })
  if (!response.ok) {
    throw new ApiError('Failed to unlink', response.status)
  }
}

export type SyncPlatform = 'swiggy_food' | 'swiggy_instamart' | 'zepto'

export interface SyncStatus {
  platform: SyncPlatform
  last_sync_at: string | null
  orders_captured_last_run: number | null
  warning: string | null
}

/**
 * Sync status per platform the caller has linked (FR-2.4). A platform with
 * no linked account at all is omitted by the backend, not returned empty.
 */
export async function getSyncStatus(): Promise<SyncStatus[]> {
  const response = await apiFetch('/sync/status')
  if (!response.ok) {
    throw new ApiError('Failed to load sync status', response.status)
  }
  return response.json()
}

/**
 * Triggers a manual sync for `platform` (AC-2). A 409 means a sync is
 * already in flight for the underlying linked account — callers should
 * treat that as a non-error state, not a failure toast.
 */
export async function triggerSync(platform: SyncPlatform): Promise<void> {
  const response = await apiFetch(`/sync/${platform}`, { method: 'POST' })
  if (!response.ok) {
    throw new ApiError(
      response.status === 409 ? 'Sync already in progress' : 'Failed to trigger sync',
      response.status,
    )
  }
}

/** Per-platform paise totals, keyed by the three sync platforms plus `combined`. */
export interface PlatformPaiseTotals {
  combined: number
  swiggy_food: number
  swiggy_instamart: number
  zepto: number
}

export interface TrendPoint {
  period_start: string
  combined_paise: number
  swiggy_food_paise: number
  swiggy_instamart_paise: number
  zepto_paise: number
}

export interface NamedSpend {
  name: string
  spend_paise: number
  order_count: number
}

export interface PlatformOrderStats {
  order_count: number
  avg_order_value_paise: number | null
}

export interface LocationSpend {
  address_id: string
  address_label: string | null
  spend_paise: number
  order_count: number
}

/** Response contract for `GET /api/v1/dashboard` (FR-3 §3). */
export interface DashboardResponse {
  generated_at: string
  has_data: boolean
  totals: {
    this_week_paise: PlatformPaiseTotals
    this_month_paise: PlatformPaiseTotals
  }
  trend: {
    weekly: TrendPoint[]
    monthly: TrendPoint[]
  }
  category_breakdown: {
    food_delivery_paise: number
    grocery_paise: number
    item_categories_paise: Record<string, number>
  }
  top_restaurants: NamedSpend[]
  top_products: NamedSpend[]
  order_stats: {
    swiggy_food: PlatformOrderStats
    swiggy_instamart: PlatformOrderStats
    zepto: PlatformOrderStats
  }
  projection: {
    month: string
    spend_to_date_paise: PlatformPaiseTotals
    days_elapsed: number
    days_in_month: number
    projected_total_paise: PlatformPaiseTotals
    label: string
  }
  location_lens: LocationSpend[]
}

/** Fetches every figure the dashboard route needs in one call (FR-3). */
export async function getDashboard(): Promise<DashboardResponse> {
  const response = await apiFetch('/dashboard')
  if (!response.ok) {
    throw new ApiError('Failed to load dashboard', response.status)
  }
  return response.json()
}

/**
 * Response contract for `POST /api/v1/query` (FR-4). `amount_paise` is the
 * raw, DB-computed figure the model's explanation is grounded in (never
 * parsed out of the model's prose, per ADR-0003) and is `null` either when
 * `has_data` is false or when the answer breaks spend down across several
 * rows rather than stating one total.
 */
export interface QueryResponse {
  amount_paise: number | null
  explanation: string
  has_data: boolean
}

/**
 * `POST /api/v1/query` uses SYSTEM.md §4's `{"error": {"code", "message"}}`
 * envelope (unlike the link routes' `detail` field), so failures need their
 * own extractor.
 */
async function queryErrorMessage(response: Response): Promise<string> {
  const fallback = 'Something went wrong answering that question — please try again.'
  try {
    const body = await response.json()
    const message = body?.error?.message
    return typeof message === 'string' && message.trim() ? message : fallback
  } catch {
    return fallback
  }
}

/** Asks a free-text spending question (FR-4 AC-1), forwarding the caller's Supabase JWT. */
export async function askQuery(question: string): Promise<QueryResponse> {
  const response = await apiFetch('/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  })
  if (!response.ok) {
    throw new ApiError(await queryErrorMessage(response), response.status)
  }
  return response.json()
}
