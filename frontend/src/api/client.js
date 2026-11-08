const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let body = await res.text()
    try {
      const detail = JSON.parse(body).detail
      if (typeof detail === 'string') body = detail
    } catch { /* not JSON: keep the text */ }
    throw new Error(`API ${res.status}: ${body}`)
  }
  return res.json()
}

export const api = {
  listHcps: () => request('/api/hcps'),
  createHcp: (data) => request('/api/hcps', { method: 'POST', body: JSON.stringify(data) }),
  listInteractions: (hcpId) => request(`/api/interactions${hcpId ? `?hcp_id=${hcpId}` : ''}`),
  createInteraction: (data) => request('/api/interactions', { method: 'POST', body: JSON.stringify(data) }),
  updateInteraction: (id, data) => request(`/api/interactions/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  chatTurn: (data) => request('/api/chat/turn', { method: 'POST', body: JSON.stringify(data) }),
  searchHcps: (q) => request(`/api/hcps?q=${encodeURIComponent(q || '')}`),
  searchMaterials: (q) => request(`/api/materials?q=${encodeURIComponent(q || '')}`),
  createMaterial: (name) => request('/api/materials', { method: 'POST', body: JSON.stringify({ name }) }),
  searchProducts: (q) => request(`/api/products?q=${encodeURIComponent(q || '')}`),
  // Raw audio body (no multipart): the server keeps it in memory only and never stores it.
  summarizeVoiceNote: async (blob) => {
    const res = await fetch(`${BASE_URL}/api/voice-notes/summarize?consent=true`, {
      method: 'POST',
      headers: { 'Content-Type': blob.type || 'audio/webm' },
      body: blob,
    })
    if (!res.ok) {
      let detail = await res.text()
      try { detail = JSON.parse(detail).detail || detail } catch { /* plain text */ }
      throw new Error(`API ${res.status}: ${detail}`)
    }
    return res.json()
  },
  listFollowUps: (interactionId) => request(`/api/interactions/${interactionId}/follow-ups`),
  suggestFollowUps: (interactionId) => request(`/api/interactions/${interactionId}/follow-ups/suggestions`, { method: 'POST' }),
  updateFollowUp: (id, data) => request(`/api/follow-ups/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
}
