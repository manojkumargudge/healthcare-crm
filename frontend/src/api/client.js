const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text()
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
}
