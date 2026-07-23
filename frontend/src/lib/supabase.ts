import { createClient, type Session, type SupabaseClient } from '@supabase/supabase-js'

let client: SupabaseClient | null = null

export function getSupabaseClient(): SupabaseClient {
  if (client) return client

  const url = import.meta.env.VITE_SUPABASE_URL
  const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

  if (!url || !anonKey) {
    throw new Error(
      'Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY. Copy .env.example to .env and fill them in.',
    )
  }

  client = createClient(url, anonKey)
  return client
}

export async function getSession(): Promise<Session | null> {
  const {
    data: { session },
  } = await getSupabaseClient().auth.getSession()
  return session
}

export function onAuthStateChange(callback: (session: Session | null) => void) {
  const {
    data: { subscription },
  } = getSupabaseClient().auth.onAuthStateChange((_event, session) => {
    callback(session)
  })
  return () => subscription.unsubscribe()
}

/** Requests a one-time passcode be emailed to `email` (signs up the user if they're new). */
export async function requestEmailOtp(email: string): Promise<void> {
  const { error } = await getSupabaseClient().auth.signInWithOtp({ email })
  if (error) throw error
}

/** Verifies the passcode sent by {@link requestEmailOtp}, establishing a session on success. */
export async function verifyEmailOtp(email: string, token: string): Promise<void> {
  const { error } = await getSupabaseClient().auth.verifyOtp({ email, token, type: 'email' })
  if (error) throw error
}

export async function signInWithGoogle(): Promise<void> {
  const { error } = await getSupabaseClient().auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: window.location.origin + import.meta.env.BASE_URL },
  })
  if (error) throw error
}

export async function signOut(): Promise<void> {
  const { error } = await getSupabaseClient().auth.signOut()
  if (error) throw error
}
