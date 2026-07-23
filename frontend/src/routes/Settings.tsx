import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  getLinkStatus,
  startLink,
  unlink as unlinkPlatform,
  type LinkPlatform,
  type LinkStatus,
} from '../lib/api'

const PLATFORM_LABELS: Record<LinkPlatform, string> = {
  swiggy: 'Swiggy',
  zepto: 'Zepto',
}

type LoadState = 'loading' | 'error' | 'ready'
type PendingAction = { platform: LinkPlatform; kind: 'link' | 'unlink' } | null
type Toast = { kind: 'success' | 'error'; text: string }

function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform as LinkPlatform] ?? platform
}

function formatLinkedAt(value: string | null): string {
  if (!value) return ''
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export default function Settings() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [links, setLinks] = useState<LinkStatus[]>([])
  const [pending, setPending] = useState<PendingAction>(null)
  const [toast, setToast] = useState<Toast | null>(null)

  const fetchStatus = useCallback(async () => {
    setLoadState('loading')
    try {
      const status = await getLinkStatus()
      setLinks(status)
      setLoadState('ready')
    } catch {
      setLoadState('error')
    }
  }, [])

  useEffect(() => {
    fetchStatus()
  }, [fetchStatus])

  useEffect(() => {
    const returnedPlatform = searchParams.get('linked')
    if (!returnedPlatform) return

    const status = searchParams.get('status')
    const label = platformLabel(returnedPlatform)
    setToast(
      status === 'ok'
        ? { kind: 'success', text: `${label} linked successfully.` }
        : { kind: 'error', text: `Couldn't link ${label}. Please try again.` },
    )
    fetchStatus()
    setSearchParams({}, { replace: true })
  }, [searchParams, setSearchParams, fetchStatus])

  async function handleLink(platform: LinkPlatform) {
    setPending({ platform, kind: 'link' })
    try {
      const { authorization_url } = await startLink(platform)
      window.location.href = authorization_url
    } catch {
      setToast({ kind: 'error', text: `Couldn't start linking ${platformLabel(platform)}. Please try again.` })
      setPending(null)
    }
  }

  async function handleUnlink(platform: LinkPlatform) {
    setPending({ platform, kind: 'unlink' })
    try {
      await unlinkPlatform(platform)
      setLinks((current) =>
        current.map((entry) =>
          entry.platform === platform ? { ...entry, linked: false, linked_at: null } : entry,
        ),
      )
    } catch {
      setToast({ kind: 'error', text: `Couldn't unlink ${platformLabel(platform)}. Please try again.` })
    } finally {
      setPending(null)
    }
  }

  return (
    <main>
      <h1>Account settings</h1>
      <p>Link your Swiggy and Zepto accounts so Overdulge can sync your order history.</p>

      {toast && (
        <p role={toast.kind === 'error' ? 'alert' : 'status'} className="toast">
          {toast.text}{' '}
          <button type="button" onClick={() => setToast(null)}>
            Dismiss
          </button>
        </p>
      )}

      {loadState === 'loading' && (
        <ul className="link-list" aria-busy="true">
          <li className="link-list__skeleton" aria-hidden="true" />
          <li className="link-list__skeleton" aria-hidden="true" />
        </ul>
      )}

      {loadState === 'error' && (
        <div role="alert">
          <p>Couldn't load link status.</p>
          <button type="button" onClick={fetchStatus}>
            Retry
          </button>
        </div>
      )}

      {loadState === 'ready' && (
        <ul className="link-list">
          {links.map((entry) => {
            const label = platformLabel(entry.platform)
            const isThisPending = pending?.platform === entry.platform
            return (
              <li key={entry.platform}>
                <div>
                  <h2>{label}</h2>
                  <p>
                    {entry.linked
                      ? `Linked · last linked ${formatLinkedAt(entry.linked_at)}`
                      : 'Not linked'}
                  </p>
                </div>
                {entry.linked ? (
                  <button
                    type="button"
                    onClick={() => handleUnlink(entry.platform)}
                    disabled={isThisPending}
                  >
                    {isThisPending && pending?.kind === 'unlink' ? 'Unlinking…' : 'Unlink'}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => handleLink(entry.platform)}
                    disabled={isThisPending}
                  >
                    {isThisPending && pending?.kind === 'link' ? 'Redirecting…' : `Link ${label}`}
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </main>
  )
}
