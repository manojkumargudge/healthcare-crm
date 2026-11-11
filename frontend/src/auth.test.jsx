// @vitest-environment jsdom
// Login, in-memory token + auth header, 401 -> sign-in page, role-based admin screens.
// Uses the REAL store and API client; only fetch is faked.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react'
import { Provider } from 'react-redux'
import App from './App'
import { createAppStore } from './store/store'
import { withOriginal } from './lib/format'

const USERS = {
  rep: { id: 'u-rep', email: 'rep.a@example.com', role: 'rep', is_active: true },
  admin: { id: 'u-admin', email: 'admin@example.com', role: 'admin', is_active: true },
}

let routes
let calls
function fakeFetch() {
  calls = []
  return vi.fn(async (url, opts = {}) => {
    const path = url.replace(/^https?:\/\/[^/]+/, '')
    calls.push({ path, opts })
    const handler = Object.entries(routes).find(([k]) => path.startsWith(k))?.[1]
    const [status, body] = handler ? handler(path, opts) : [404, { detail: 'not found' }]
    return { ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) }
  })
}

function signedInRoutes(user, overrides = {}) {
  return {
    '/auth/login': (_p, opts) => {
      const form = new URLSearchParams(opts.body)
      return form.get('password') === 'right-password-123'
        ? [200, { access_token: `token-for-${user.id}`, token_type: 'bearer', expires_in: 900, user }]
        : [401, { detail: 'Incorrect email or password' }]
    },
    '/api/hcps': () => [200, [{ id: 'h1', name: 'Dr. A', specialty: 'Cardiology' }]],
    '/api/interactions': () => [200, []],
    ...overrides,
  }
}

const renderApp = (store = createAppStore()) => ({ store, ...render(<Provider store={store}><App /></Provider>) })

async function signIn(password = 'right-password-123') {
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'rep.a@example.com' } })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Sign in' })) })
}

beforeEach(() => {
  routes = signedInRoutes(USERS.rep)
  vi.stubGlobal('fetch', fakeFetch())
  Element.prototype.scrollIntoView = vi.fn() // not implemented by jsdom; the chat panel calls it
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('login', () => {
  it('shows the sign-in page first and logs in with the OAuth2 password form', async () => {
    renderApp()
    expect(screen.getByRole('form', { name: 'Sign in' })).toBeTruthy()
    await signIn()
    const login = calls.find((c) => c.path === '/auth/login')
    expect(login.opts.headers['Content-Type']).toBe('application/x-www-form-urlencoded')
    expect(login.opts.headers.Authorization).toBeUndefined()
    expect(new URLSearchParams(login.opts.body).get('username')).toBe('rep.a@example.com')
    expect(await screen.findByRole('button', { name: 'Interactions' })).toBeTruthy()
    expect(screen.getByText('rep.a@example.com')).toBeTruthy()
  })

  it('shows an error for wrong credentials', async () => {
    renderApp()
    await signIn('wrong')
    expect(screen.getByRole('alert').textContent).toBe('Incorrect email or password.')
  })

  it('sends the in-memory token as a bearer header and never stores it in web storage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    renderApp()
    await signIn()
    await waitFor(() => expect(calls.some((c) => c.path === '/api/hcps')).toBe(true))
    const hcps = calls.find((c) => c.path === '/api/hcps')
    expect(hcps.opts.headers.Authorization).toBe('Bearer token-for-u-rep')
    expect(setItem).not.toHaveBeenCalled()
  })

  it('any 401 signs the rep out and explains why', async () => {
    const { store } = renderApp()
    await signIn()
    await screen.findByRole('button', { name: 'Interactions' })
    routes['/api/hcps'] = () => [401, { detail: 'Not authenticated' }]
    const { fetchHcps } = await import('./store/hcpSlice')
    await act(async () => { await store.dispatch(fetchHcps()) })
    expect(await screen.findByRole('form', { name: 'Sign in' })).toBeTruthy()
    expect(screen.getByRole('status').textContent).toMatch(/session has expired/)
    expect(store.getState().auth.token).toBeNull()
    expect(store.getState().hcps.items).toEqual([]) // nothing from the old session stays
  })

  it('sign out clears the session', async () => {
    const { store } = renderApp()
    await signIn()
    await screen.findByRole('button', { name: 'Sign out' })
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(screen.getByRole('form', { name: 'Sign in' })).toBeTruthy()
    expect(store.getState().auth).toMatchObject({ token: null, user: null })
  })
})

describe('role-based screens', () => {
  it('reps do not see the Admin screen', async () => {
    renderApp()
    await signIn()
    await screen.findByRole('button', { name: 'Interactions' })
    expect(screen.queryByRole('button', { name: 'Admin' })).toBeNull()
  })

  it('admins can approve materials, manage products and users', async () => {
    let approved = false
    routes = signedInRoutes(USERS.admin, {
      '/api/admin/materials/pending': () => [200, approved ? [] : [{ id: 'm1', name: 'Rep slide deck', approved: false }]],
      '/api/admin/materials/m1/approve': () => { approved = true; return [200, { material: { id: 'm1', name: 'Rep slide deck', approved: true }, interactions_rechecked: 2, flags_cleared: 1 }] },
      '/api/products': () => [200, [{ id: 'p1', name: 'CardioMax' }]],
      '/api/admin/products': (_p, o) => [201, { id: 'p2', name: JSON.parse(o.body).name }],
      '/api/admin/reps': (_p, o) => (o.method === 'POST' ? [201, { id: 'u9', ...JSON.parse(o.body), is_active: true }] : [200, [USERS.admin, USERS.rep]]),
    })
    renderApp()
    await signIn()
    fireEvent.click(await screen.findByRole('button', { name: 'Admin' }))

    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    expect(await screen.findByText(/2 interaction\(s\) re-checked, 1 compliance flag\(s\) cleared/)).toBeTruthy()

    fireEvent.change(screen.getByLabelText('New product name'), { target: { value: 'NeuroCalm' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Add product' })) })
    const addProduct = calls.find((c) => c.path === '/api/admin/products' && c.opts.method === 'POST')
    expect(JSON.parse(addProduct.opts.body)).toEqual({ name: 'NeuroCalm' })
    expect(addProduct.opts.headers.Authorization).toBe('Bearer token-for-u-admin')

    fireEvent.change(screen.getByLabelText('Email', { selector: '.admin-inline input' }), { target: { value: 'new.rep@example.com' } })
    fireEvent.change(screen.getByLabelText('Initial password'), { target: { value: 'a-long-password-1' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Add rep' })) })
    const createUser = calls.find((c) => c.path === '/api/admin/reps' && c.opts.method === 'POST')
    expect(JSON.parse(createUser.opts.body)).toEqual({ email: 'new.rep@example.com', password: 'a-long-password-1', role: 'rep' })
    expect(screen.getByText('rep.a@example.com')).toBeTruthy()
  })
})

describe('fuzzy product matches keep the original text', () => {
  it('formats catalog names with what the rep typed', () => {
    const matches = [{ field: 'products_discussed', product: 'CardioMax', from: 'cardiomx' }]
    expect(withOriginal('CardioMax', 'products_discussed', matches)).toBe("CardioMax (from 'cardiomx')")
    expect(withOriginal('CardioMax', 'samples_dropped', matches)).toBe('CardioMax')
    expect(withOriginal('OncoBoost', 'products_discussed', matches)).toBe('OncoBoost')
  })
})

describe('history edit rules match the API', () => {
  const rows = [
    { id: 'mine', owner_id: 'u-mgr', interaction_type: 'Visit', interaction_date: '2026-11-08', raw_notes: 'my notes', edit_history: [] },
    { id: 'theirs', owner_id: 'u-rep', interaction_type: 'Call', interaction_date: '2026-11-07', raw_notes: 'rep notes', edit_history: [] },
  ]
  const storeFor = (user) => {
    const s = createAppStore({ auth: { token: 't', user, status: 'succeeded', error: null, notice: null } })
    s.dispatch({ type: 'interactions/fetch/fulfilled', payload: { items: rows, total: rows.length, reps: [] } })
    return s
  }

  it('a manager can edit only their own rows; an admin can edit all', async () => {
    const { default: InteractionHistory } = await import('./components/InteractionHistory')
    render(<Provider store={storeFor({ id: 'u-mgr', email: 'm@x', role: 'manager' })}><InteractionHistory /></Provider>)
    expect(screen.getAllByRole('button', { name: 'Edit Interaction' })).toHaveLength(1)
    cleanup()
    render(<Provider store={storeFor({ id: 'u-adm', email: 'a@x', role: 'admin' })}><InteractionHistory /></Provider>)
    expect(screen.getAllByRole('button', { name: 'Edit Interaction' })).toHaveLength(2)
  })

  it('a failed save keeps the editor open and shows why', async () => {
    // more specific route first: the fake router matches by prefix, in order
    routes = { '/api/interactions/mine': () => [403, { detail: 'Not allowed to change this interaction' }], ...routes }
    const { default: InteractionHistory } = await import('./components/InteractionHistory')
    render(<Provider store={storeFor({ id: 'u-mgr', email: 'm@x', role: 'manager' })}><InteractionHistory /></Provider>)
    fireEvent.click(screen.getByRole('button', { name: 'Edit Interaction' }))
    fireEvent.change(screen.getByLabelText('Edit notes'), { target: { value: 'changed' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    expect(screen.getByRole('alert').textContent).toContain('Not allowed to change this interaction')
    expect(screen.getByLabelText('Edit notes').value).toBe('changed')
  })
})
