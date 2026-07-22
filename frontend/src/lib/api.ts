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
