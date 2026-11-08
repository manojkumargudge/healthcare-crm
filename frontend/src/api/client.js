const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

// The access token lives in memory only (Redux + this module), never in localStorage:
// a page reload means signing in again. Tokens are short-lived; any 401 signs the rep out.
let authToken = null
let onUnauthorized = () => {}

export function setAuthToken(token) {
  authToken = token || null
}

export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler || (() => {})
}

export const RATE_LIMIT_FALLBACK = 'Too many AI requests right now. Please try again in a minute.'

async function parseError(res) {
  let body = await res.text()
  let detail = null
  try {
    const parsed = JSON.parse(body).detail
    if (typeof parsed === 'string') detail = parsed
    else if (Array.isArray(parsed)) detail = parsed.map((d) => d.msg).join('; ')
  } catch { /* not JSON: keep the text */ }
  if (res.status === 429) {
    // AI limits (per minute or the daily cap): the server's message is written for
    // people, so show it as-is instead of "API 429: ...".
    const err = new Error(detail || RATE_LIMIT_FALLBACK)
    err.name = 'RateLimitError' // survives Redux Toolkit's error serialization
    err.status = 429
    return err
  }
  const err = new Error(`API ${res.status}: ${detail ?? body}`)
  err.status = res.status
  return err
}

async function send(path, { headers = {}, auth = true, ...options } = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: { ...(auth && authToken ? { Authorization: `Bearer ${authToken}` } : {}), ...headers },
  })
  if (res.status === 401 && auth) {
    onUnauthorized()
  }
  if (!res.ok) throw await parseError(res)
  return res.status === 204 ? null : res.json()
}

const json = (path, method = 'GET', body) =>
  send(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  })

export const api = {
  // OAuth2 password flow: form-encoded, username = the rep's email.
  login: (email, password) =>
    send('/auth/login', {
      method: 'POST',
      auth: false,
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ username: email, password }).toString(),
    }),
  me: () => json('/auth/me'),
  meta: () => send('/api/meta', { auth: false }),

  listHcps: () => json('/api/hcps'),
  createHcp: (data) => json('/api/hcps', 'POST', data),
  listInteractions: (hcpId) => json(`/api/interactions${hcpId ? `?hcp_id=${hcpId}` : ''}`),
  createInteraction: (data) => json('/api/interactions', 'POST', data),
  updateInteraction: (id, data) => json(`/api/interactions/${id}`, 'PATCH', data),
  chatTurn: (data) => json('/api/chat/turn', 'POST', data),
  searchHcps: (q) => json(`/api/hcps?q=${encodeURIComponent(q || '')}`),
  searchMaterials: (q) => json(`/api/materials?q=${encodeURIComponent(q || '')}`),
  createMaterial: (name) => json('/api/materials', 'POST', { name }),
  searchProducts: (q) => json(`/api/products?q=${encodeURIComponent(q || '')}`),
  // Raw audio body (no multipart): the server keeps it in memory only and never stores it.
  summarizeVoiceNote: (blob) =>
    send('/api/voice-notes/summarize?consent=true', {
      method: 'POST',
      headers: { 'Content-Type': blob.type || 'audio/webm' },
      body: blob,
    }),
  listFollowUps: (interactionId) => json(`/api/interactions/${interactionId}/follow-ups`),
  suggestFollowUps: (interactionId) => json(`/api/interactions/${interactionId}/follow-ups/suggestions`, 'POST'),
  updateFollowUp: (id, data) => json(`/api/follow-ups/${id}`, 'PATCH', data),

  // Admin
  pendingMaterials: () => json('/api/admin/materials/pending'),
  approveMaterial: (id) => json(`/api/admin/materials/${id}/approve`, 'POST'),
  listProducts: () => json('/api/products?limit=100'),
  addProduct: (name) => json('/api/admin/products', 'POST', { name }),
  renameProduct: (id, name) => json(`/api/admin/products/${id}`, 'PATCH', { name }),
  deleteProduct: (id) => json(`/api/admin/products/${id}`, 'DELETE'),
  listUsers: () => json('/api/admin/reps'),
  createUser: (data) => json('/api/admin/reps', 'POST', data),
  updateUser: (id, data) => json(`/api/admin/reps/${id}`, 'PATCH', data),
}
