import { useEffect, useState } from 'react'
import { api } from '../api/client'

// Public app settings (GET /api/meta, no sign-in needed), fetched once per page load.
let cached = null
let inflight = null

export function loadMeta() {
  if (cached) return Promise.resolve(cached)
  if (!inflight) {
    inflight = api
      .meta()
      .then((m) => (cached = m))
      .catch(() => null) // banner is optional: never block the app on it
      .finally(() => { inflight = null })
  }
  return inflight
}

export function resetMetaCache() {
  cached = null
  inflight = null
}

export function useAppMeta() {
  const [meta, setMeta] = useState(cached)
  useEffect(() => {
    let alive = true
    loadMeta().then((m) => { if (alive && m) setMeta(m) })
    return () => { alive = false }
  }, [])
  return meta
}
