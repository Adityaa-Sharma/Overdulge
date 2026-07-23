import { getSession } from './supabase'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

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
    window.location.assign('/login')
    throw new ApiError('Unauthorized', 401)
  }

  return response
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
    throw new ApiError('Failed to start link', response.status)
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
