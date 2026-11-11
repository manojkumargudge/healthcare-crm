// @vitest-environment jsdom
// Demo mode banner + read-only user management, friendly AI-limit (429) messages,
// and the Rep line/filter on Interactions. Real store and API client; only fetch is faked.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act, within } from '@testing-library/react'
import { Provider } from 'react-redux'
import App from './App'
import { createAppStore } from './store/store'
import { resetMetaCache } from './lib/meta'
import { api, RATE_LIMIT_FALLBACK } from './api/client'
import { sendChatMessage } from './store/chatSlice'
import { generateSuggestions } from './store/followUpsSlice'

const USERS = {
  rep: { id: 'u-rep', email: 'demo-rep@example.com', role: 'rep', is_active: true },
  manager: { id: 'u-mgr', email: 'demo-manager@example.com', role: 'manager', is_active: true },
  admin: { id: 'u-admin', email: 'demo-admin@example.com', role: 'admin', is_active: true },
}
const DAILY =
  'The daily limit of 500 AI requests has been reached. AI features (chat, follow-up suggestions, voice notes) ' +
  'are back after midnight UTC; you can still log interactions with the form.'

const row = (id, owner, extra = {}) => ({
  id, hcp_id: 'h1', owner_id: owner.id, owner_email: owner.email, interaction_type: 'Visit',
  interaction_date: '2026-11-01', products_discussed: [], samples_dropped: [], materials_shared: [],
  key_topics: [], attendees: [], raw_notes: `notes ${id}`, compliance_flag: false, edit_history: [],
  created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z', ...extra,
})

let routes
let calls
const repCounts = (rows) => {
  const by = {}
  for (const r of rows) by[r.owner_id] = { owner_id: r.owner_id, email: r.owner_email, count: (by[r.owner_id]?.count || 0) + 1 }
  return Object.values(by).sort((a, b) => a.email.localeCompare(b.email))
}
function fakeFetch() {
  calls = []
  return vi.fn(async (url, opts = {}) => {
    const path = url.replace(/^https?:\/\/[^/]+/, '')
    calls.push({ path, opts })
    const handler = Object.entries(routes).find(([k]) => path.startsWith(k))?.[1]
    const [status, body, headers] = handler ? handler(path, opts) : [404, { detail: 'not found' }]
    const text = typeof body === 'string' ? body : JSON.stringify(body)
    const hdrs = headers ? headers(path) : {}
    return {
      ok: status < 400, status, json: async () => JSON.parse(text), text: async () => text,
      headers: { get: (k) => hdrs[k.toLowerCase()] ?? null },
    }
  })
}

function setup(user, { demo = false, interactions = [], extra = {} } = {}) {
  routes = {
    ...extra,
    '/api/meta': () => [200, { demo_mode: demo, llm_daily_cap: 500 }],
    '/auth/login': () => [200, { access_token: 't', token_type: 'bearer', expires_in: 900, user }],
    '/api/hcps': () => [200, [{ id: 'h1', name: 'Dr. A', specialty: 'Cardiology' }]],
    // Like the server: owner_id filter, limit/offset paging, total in X-Total-Count.
    '/api/interactions/reps': () => [200, repCounts(interactions)],
    '/api/interactions': (path) => {
      const q = new URL(path, 'http://x').searchParams
      const rows = interactions.filter((i) => !q.get('owner_id') || i.owner_id === q.get('owner_id'))
      const offset = Number(q.get('offset') || 0)
      const page = rows.slice(offset, offset + Number(q.get('limit') || 25))
      return [200, page, () => ({ 'x-total-count': String(rows.length) })]
    },
    '/api/admin/materials/pending': () => [200, []],
    '/api/products': () => [200, []],
    '/api/admin/reps': () => [200, Object.values(USERS)],
  }
  Object.assign(routes, extra) // explicit overrides win
}

const renderApp = (store = createAppStore()) => ({ store, ...render(<Provider store={store}><App /></Provider>) })

async function signIn() {
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'x@example.com' } })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'whatever-password' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Sign in' })) })
  await screen.findByRole('button', { name: 'Sign out' })
}

beforeEach(() => {
  resetMetaCache()
  vi.stubGlobal('fetch', fakeFetch())
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('demo mode', () => {
  it('shows the banner on the sign-in page and after signing in', async () => {
    setup(USERS.rep, { demo: true })
    renderApp()
    const banner = await screen.findByRole('note', { name: 'Demo mode' })
    expect(banner.textContent).toMatch(/resets nightly/)
    expect(banner.textContent).toMatch(/500 requests/)
    expect(calls.find((c) => c.path === '/api/meta').opts.headers.Authorization).toBeUndefined()
    await signIn()
    expect(screen.getByRole('note', { name: 'Demo mode' })).toBeTruthy()
  })

  it('shows no banner when demo mode is off', async () => {
    setup(USERS.rep, { demo: false })
    renderApp()
    await act(async () => {})
    expect(calls.some((c) => c.path === '/api/meta')).toBe(true)
    expect(screen.queryByRole('note', { name: 'Demo mode' })).toBeNull()
  })

  it('still shows the sign-in page when /api/meta fails', async () => {
    setup(USERS.rep, { extra: { '/api/meta': () => [500, { detail: 'boom' }] } })
    renderApp()
    await act(async () => {})
    expect(screen.queryByRole('note', { name: 'Demo mode' })).toBeNull()
    expect(screen.getByRole('form', { name: 'Sign in' })).toBeTruthy()
  })

  it('makes user management read-only on the Admin screen', async () => {
    setup(USERS.admin, { demo: true })
    renderApp()
    await signIn()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Admin' })) })
    const section = screen.getByRole('region', { name: 'Reps' })
    expect(await within(section).findByText('demo-rep@example.com')).toBeTruthy()
    expect(within(section).getByTestId('users-read-only').textContent).toMatch(/read-only/)
    expect(within(section).queryByRole('button', { name: 'Add rep' })).toBeNull()
    expect(within(section).queryByRole('button', { name: /Deactivate|Reset password/ })).toBeNull()
    expect(within(section).queryByRole('combobox', { name: /Role for/ })).toBeNull()
  })

  it('keeps user management editable outside demo mode', async () => {
    setup(USERS.admin, { demo: false })
    renderApp()
    await signIn()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Admin' })) })
    const section = screen.getByRole('region', { name: 'Reps' })
    await within(section).findByText('demo-rep@example.com')
    expect(within(section).getByRole('button', { name: 'Add rep' })).toBeTruthy()
    expect(within(section).getAllByRole('button', { name: 'Deactivate' })).toHaveLength(3)
  })
})

describe('AI limits (429)', () => {
  it("the API client uses the server's message, without the status prefix", async () => {
    setup(USERS.rep, { extra: { '/api/chat/turn': () => [429, { detail: DAILY }] } })
    const err = await api.chatTurn({}).catch((e) => e)
    expect(err.name).toBe('RateLimitError')
    expect(err.message).toBe(DAILY)
    setup(USERS.rep, { extra: { '/api/chat/turn': () => [429, 'not json'] } })
    expect((await api.chatTurn({}).catch((e) => e)).message).toBe(RATE_LIMIT_FALLBACK)
  })

  it('chat shows the friendly message instead of "Something went wrong"', async () => {
    setup(USERS.rep, { extra: { '/api/chat/turn': () => [429, { detail: DAILY }] } })
    const store = createAppStore()
    await store.dispatch(sendChatMessage({ sessionId: 's', message: 'hi', hcpId: 'h1' }))
    const last = store.getState().chat.messages.at(-1)
    expect(last.content).toBe(DAILY)
    expect(last.rateLimited).toBe(true)
  })

  it('other chat errors keep the generic prefix', async () => {
    setup(USERS.rep, { extra: { '/api/chat/turn': () => [500, { detail: 'boom' }] } })
    const store = createAppStore()
    await store.dispatch(sendChatMessage({ sessionId: 's', message: 'hi', hcpId: 'h1' }))
    expect(store.getState().chat.messages.at(-1).content).toBe('Something went wrong: API 500: boom')
  })

  it('follow-up suggestions flag a rate limit so the UI shows the message alone', async () => {
    setup(USERS.rep, { extra: { '/api/interactions/i1/follow-ups/suggestions': () => [429, { detail: DAILY }] } })
    const store = createAppStore()
    await store.dispatch(generateSuggestions('i1'))
    expect(store.getState().followUps.rateLimited).toBe(true)
    expect(store.getState().followUps.error).toBe(DAILY)
  })
})

describe('Rep line and filter on Interactions', () => {
  const interactions = [
    row('a1', USERS.rep), row('a2', USERS.rep), row('m1', USERS.manager), row('x1', USERS.admin),
  ]

  it('managers see each interaction’s rep and can filter by rep', async () => {
    setup(USERS.manager, { interactions })
    renderApp()
    await signIn()
    const filter = await screen.findByLabelText('Rep')
    expect(screen.getAllByText(/^Rep: /)).toHaveLength(4)
    expect([...filter.options].map((o) => o.textContent)).toEqual([
      'All reps (4)', 'demo-admin@example.com (1)', 'demo-manager@example.com (1)', 'demo-rep@example.com (2)',
    ])
    await act(async () => { fireEvent.change(filter, { target: { value: USERS.rep.id } }) })
    expect(screen.getAllByText('Rep: demo-rep@example.com')).toHaveLength(2)
    expect(screen.queryByText('Rep: demo-manager@example.com')).toBeNull()
    // The filter runs on the server, not by hiding rows in the browser.
    expect(calls.some((c) => c.path.includes(`owner_id=${USERS.rep.id}`))).toBe(true)
    await act(async () => { fireEvent.change(filter, { target: { value: '' } }) })
    expect(screen.getAllByText(/^Rep: /)).toHaveLength(4)
  })

  it('admins get the same column and filter', async () => {
    setup(USERS.admin, { interactions })
    renderApp()
    await signIn()
    expect(await screen.findByLabelText('Rep')).toBeTruthy()
    expect(screen.getAllByText(/^Rep: /)).toHaveLength(4)
  })

  it('shows 25 at a time and loads the next page on "Load more"', async () => {
    const many = Array.from({ length: 30 }, (_, n) => row(`r${n}`, USERS.rep))
    setup(USERS.manager, { interactions: many })
    renderApp()
    await signIn()
    await screen.findByText('Showing 25 of 30')
    expect(screen.getAllByText(/^Rep: /)).toHaveLength(25)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Load more' })) })
    expect(screen.getAllByText(/^Rep: /)).toHaveLength(30)
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull()
    expect(calls.some((c) => c.path.includes('offset=25'))).toBe(true)
  })

  it('reps see neither (they only have their own interactions)', async () => {
    setup(USERS.rep, { interactions: interactions.slice(0, 2) })
    renderApp()
    await signIn()
    await screen.findByText('"notes a1"')
    expect(screen.queryByLabelText('Rep')).toBeNull()
    expect(screen.queryByText(/^Rep: /)).toBeNull()
  })
})
