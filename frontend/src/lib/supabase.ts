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

// Email OTP sign-in was removed: this Supabase project has no custom SMTP, so
// its built-in mailer rate-limits every code request (429
// over_email_send_rate_limit) and the flow could never reliably deliver a
// code. Google is the only sign-in method. Re-add email here (and a UI for it)
// once an SMTP provider is configured.

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
