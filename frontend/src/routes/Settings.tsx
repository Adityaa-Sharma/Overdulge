import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  ApiError,
  getLinkStatus,
  getSyncStatus,
  startLink,
  triggerSync,
  unlink as unlinkPlatform,
  type LinkPlatform,
  type LinkStatus,
  type SyncPlatform,
  type SyncStatus,
} from '../lib/api'

const PLATFORM_LABELS: Record<LinkPlatform, string> = {
  swiggy: 'Swiggy',
  zepto: 'Zepto',
}

const SYNC_PLATFORM_LABELS: Record<SyncPlatform, string> = {
  swiggy_food: 'Swiggy Food',
  swiggy_instamart: 'Swiggy Instamart',
  zepto: 'Zepto',
}

type LoadState = 'loading' | 'error' | 'ready'
type PendingAction = { platform: LinkPlatform; kind: 'link' | 'unlink' } | null
type Toast = { kind: 'success' | 'error'; text: string }
type SyncMessage = { kind: 'conflict' | 'error'; text: string }

function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform as LinkPlatform] ?? platform
}

function syncPlatformLabel(platform: SyncPlatform): string {
  return SYNC_PLATFORM_LABELS[platform] ?? platform
}

function formatLinkedAt(value: string | null): string {
  if (!value) return ''
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function formatLastSync(value: string | null): string {
  if (!value) return 'Never synced'
  return `Last synced ${new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}`
}

export default function Settings() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [links, setLinks] = useState<LinkStatus[]>([])
  const [pending, setPending] = useState<PendingAction>(null)
  const [toast, setToast] = useState<Toast | null>(null)

  const [syncLoadState, setSyncLoadState] = useState<LoadState>('loading')
  const [syncStatuses, setSyncStatuses] = useState<SyncStatus[]>([])
  const [syncPending, setSyncPending] = useState<Partial<Record<SyncPlatform, boolean>>>({})
  const [syncMessages, setSyncMessages] = useState<Partial<Record<SyncPlatform, SyncMessage>>>({})

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

  const fetchSyncStatus = useCallback(async () => {
    setSyncLoadState('loading')
    try {
      const status = await getSyncStatus()
      setSyncStatuses(status)
      setSyncLoadState('ready')
    } catch {
      setSyncLoadState('error')
    }
  }, [])

  useEffect(() => {
    fetchSyncStatus()
  }, [fetchSyncStatus])

  async function handleSyncNow(platform: SyncPlatform) {
    setSyncPending((current) => ({ ...current, [platform]: true }))
    setSyncMessages((current) => ({ ...current, [platform]: undefined }))
    try {
      await triggerSync(platform)
      await fetchSyncStatus()
    } catch (error) {
      const label = syncPlatformLabel(platform)
      const message: SyncMessage =
        error instanceof ApiError && error.status === 409
          ? { kind: 'conflict', text: 'A sync is already in progress.' }
          : { kind: 'error', text: `Couldn't sync ${label}. Please try again.` }
      setSyncMessages((current) => ({ ...current, [platform]: message }))
    } finally {
      setSyncPending((current) => ({ ...current, [platform]: false }))
    }
  }

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

      <h2>Linked accounts</h2>

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
                  <h3>{label}</h3>
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

      <h2>Sync status</h2>

      {syncLoadState === 'loading' && (
        <ul className="link-list" aria-busy="true">
          <li className="link-list__skeleton" aria-hidden="true" />
          <li className="link-list__skeleton" aria-hidden="true" />
        </ul>
      )}

      {syncLoadState === 'error' && (
        <div role="alert">
          <p>Couldn't load sync status.</p>
          <button type="button" onClick={fetchSyncStatus}>
            Retry
          </button>
        </div>
      )}

      {syncLoadState === 'ready' && syncStatuses.length === 0 && (
        <p>Link an account above to start syncing your order history.</p>
      )}

      {syncLoadState === 'ready' && syncStatuses.length > 0 && (
        <ul className="link-list">
          {syncStatuses.map((entry) => {
            const label = syncPlatformLabel(entry.platform)
            const isPending = Boolean(syncPending[entry.platform])
            const message = syncMessages[entry.platform]
            return (
              <li key={entry.platform}>
                <div>
                  <h3>{label}</h3>
                  <p>{formatLastSync(entry.last_sync_at)}</p>
                  {entry.orders_captured_last_run !== null && (
                    <p>{entry.orders_captured_last_run} orders captured in the last run</p>
                  )}
                  {entry.warning && <p role="note">{entry.warning}</p>}
                  {message && (
                    <p role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => handleSyncNow(entry.platform)}
                  disabled={isPending}
                >
                  {isPending ? 'Syncing…' : 'Sync now'}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </main>
  )
}
