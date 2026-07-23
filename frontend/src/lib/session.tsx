import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import type { Session } from '@supabase/supabase-js'
import { getSession, onAuthStateChange, signOut } from './supabase'

export type SessionStatus = 'checking' | 'authenticated' | 'anonymous'

interface SessionContextValue {
  session: Session | null
  status: SessionStatus
  logout: () => Promise<void>
}

const SessionContext = createContext<SessionContextValue | undefined>(undefined)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [status, setStatus] = useState<SessionStatus>('checking')

  useEffect(() => {
    let cancelled = false

    getSession().then((initialSession) => {
      if (cancelled) return
      setSession(initialSession)
      setStatus(initialSession ? 'authenticated' : 'anonymous')
    })

    const unsubscribe = onAuthStateChange((nextSession) => {
      if (cancelled) return
      setSession(nextSession)
      setStatus(nextSession ? 'authenticated' : 'anonymous')
    })

    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [])

  return (
    <SessionContext.Provider value={{ session, status, logout: signOut }}>
      {children}
    </SessionContext.Provider>
  )
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext)
  if (!context) {
    throw new Error('useSession must be used within a SessionProvider')
  }
  return context
}
