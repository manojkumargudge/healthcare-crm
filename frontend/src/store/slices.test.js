// Smoke tests: one per Redux slice. The API client is mocked, so nothing hits the network.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { configureStore } from '@reduxjs/toolkit'

vi.mock('../api/client', () => ({
  api: {
    listHcps: vi.fn(),
    listInteractions: vi.fn(),
    listInteractionReps: vi.fn(),
    createInteraction: vi.fn(),
    updateInteraction: vi.fn(),
    chatTurn: vi.fn(),
    listFollowUps: vi.fn(),
    suggestFollowUps: vi.fn(),
    updateFollowUp: vi.fn(),
  },
}))

import { api } from '../api/client'
import hcpReducer, { fetchHcps, selectHcp } from './hcpSlice'
import interactionsReducer, { fetchInteractions, submitStructuredInteraction, editInteraction } from './interactionsSlice'
import chatReducer, { sendChatMessage, resetSession } from './chatSlice'
import followUpsReducer, { generateSuggestions, acceptFollowUp, dismissFollowUp } from './followUpsSlice'
import draftReducer, { setDraftInteraction, updateDraftField, clearDraftInteraction } from './draftInteractionSlice'

const makeStore = (reducer) => configureStore({ reducer: { s: reducer } })

beforeEach(() => vi.clearAllMocks())

describe('hcpSlice', () => {
  it('loads HCPs, auto-selects the first, and supports manual selection', async () => {
    api.listHcps.mockResolvedValue([{ id: 'h1', name: 'Dr. A' }, { id: 'h2', name: 'Dr. B' }])
    const store = makeStore(hcpReducer)
    expect(store.getState().s).toEqual({ items: [], selectedId: null, status: 'idle' })

    await store.dispatch(fetchHcps())
    expect(store.getState().s.status).toBe('succeeded')
    expect(store.getState().s.items).toHaveLength(2)
    expect(store.getState().s.selectedId).toBe('h1')

    store.dispatch(selectHcp('h2'))
    expect(store.getState().s.selectedId).toBe('h2')
  })
})

describe('interactionsSlice', () => {
  it('fetches, prepends a submitted interaction, and replaces an edited one', async () => {
    api.listInteractions.mockResolvedValue({ items: [{ id: 'i1', hcp_id: 'h1', raw_notes: 'old' }], total: 1 })
    api.createInteraction.mockResolvedValue({ id: 'i2', hcp_id: 'h1', raw_notes: 'new' })
    api.updateInteraction.mockResolvedValue({ id: 'i1', raw_notes: 'edited' })
    const store = makeStore(interactionsReducer)

    await store.dispatch(fetchInteractions('h1'))
    expect(api.listInteractions).toHaveBeenCalledWith('h1', { ownerId: null })
    expect(api.listInteractionReps).not.toHaveBeenCalled() // no signed-in manager in this store

    await store.dispatch(submitStructuredInteraction({ hcp_id: 'h1' }))
    expect(store.getState().s.items.map((i) => i.id)).toEqual(['i2', 'i1'])
    expect(store.getState().s.lastSubmitted.id).toBe('i2')

    await store.dispatch(editInteraction({ id: 'i1', updates: { raw_notes: 'edited' } }))
    expect(api.updateInteraction).toHaveBeenCalledWith('i1', { raw_notes: 'edited' })
    expect(store.getState().s.items.find((i) => i.id === 'i1').raw_notes).toBe('edited')
  })
})

describe('chatSlice', () => {
  it('appends user + assistant messages and resets the session', async () => {
    api.chatTurn.mockResolvedValue({ reply: 'Logged.', tool_calls: [{ tool: 'log_interaction' }], interaction: null })
    const store = makeStore(chatReducer)
    const firstSession = store.getState().s.sessionId
    expect(firstSession).toBeTruthy()

    await store.dispatch(sendChatMessage({ sessionId: firstSession, message: 'hi', hcpId: 'h1' }))
    expect(api.chatTurn).toHaveBeenCalledWith({ session_id: firstSession, message: 'hi', hcp_id: 'h1' })
    const { messages, status } = store.getState().s
    expect(status).toBe('succeeded')
    expect(messages.map((m) => m.role)).toEqual(['user', 'assistant'])
    expect(messages[1].content).toBe('Logged.')

    store.dispatch(resetSession())
    expect(store.getState().s.messages).toEqual([])
    expect(store.getState().s.sessionId).not.toBe(firstSession)
  })

  it('records a failed turn as an error message', async () => {
    api.chatTurn.mockRejectedValue(new Error('API 500'))
    const store = makeStore(chatReducer)
    await store.dispatch(sendChatMessage({ sessionId: 's', message: 'hi' }))
    const last = store.getState().s.messages.at(-1)
    expect(store.getState().s.status).toBe('failed')
    expect(last.error).toBe(true)
    expect(last.content).toContain('API 500')
  })
})

describe('draftInteractionSlice', () => {
  it('merges, updates a single field, and clears back to defaults', () => {
    let state = draftReducer(undefined, { type: '@@init' })
    expect(state.interaction_type).toBe('Visit')
    expect(state.interaction_date).toMatch(/^\d{4}-\d{2}-\d{2}$/)

    state = draftReducer(state, setDraftInteraction({ raw_notes: 'notes', products_discussed: ['A'] }))
    state = draftReducer(state, updateDraftField({ field: 'hcp_sentiment', value: 'Positive' }))
    expect(state).toMatchObject({ raw_notes: 'notes', products_discussed: ['A'], hcp_sentiment: 'Positive' })

    state = draftReducer(state, clearDraftInteraction())
    expect(state.raw_notes).toBe('')
    expect(state.products_discussed).toEqual([])
  })
})

describe('followUpsSlice', () => {
  it('stores suggestions per interaction and updates accepted/dismissed ones in place', async () => {
    const s1 = { id: 'f1', interaction_id: 'i1', reason: 'Send PDF', status: 'Suggested' }
    const s2 = { id: 'f2', interaction_id: 'i1', reason: 'Call back', status: 'Suggested' }
    api.suggestFollowUps.mockResolvedValue([s1, s2])
    api.listFollowUps.mockResolvedValue([s1, s2])
    api.updateFollowUp.mockImplementation(async (id, { status }) => ({ ...(id === 'f1' ? s1 : s2), status }))
    const store = makeStore(followUpsReducer)

    await store.dispatch(generateSuggestions('i1'))
    expect(api.suggestFollowUps).toHaveBeenCalledWith('i1')
    expect(store.getState().s.byInteraction.i1.map((f) => f.id)).toEqual(['f1', 'f2'])

    await store.dispatch(acceptFollowUp({ id: 'f1' }))
    await store.dispatch(dismissFollowUp({ id: 'f2' }))
    expect(store.getState().s.byInteraction.i1.map((f) => f.status)).toEqual(['Pending', 'Dismissed'])
  })

  it('records a failure message', async () => {
    api.suggestFollowUps.mockRejectedValue(new Error('API 502'))
    const store = makeStore(followUpsReducer)
    await store.dispatch(generateSuggestions('i9'))
    expect(store.getState().s).toMatchObject({ status: 'failed', error: 'API 502' })
  })
})

describe('followUpsSlice concurrency', () => {
  it('ignores a second generate for the same interaction while one is in flight', async () => {
    api.suggestFollowUps.mockImplementation(() => new Promise((r) => setTimeout(() => r([]), 20)))
    api.listFollowUps.mockResolvedValue([])
    const store = configureStore({ reducer: { followUps: followUpsReducer } })
    const first = store.dispatch(generateSuggestions('i1'))
    const second = store.dispatch(generateSuggestions('i1')) // e.g. StrictMode double effect
    await Promise.all([first, second])
    expect(api.suggestFollowUps).toHaveBeenCalledTimes(1)
    expect(store.getState().followUps.inFlight).toEqual({})
    await store.dispatch(generateSuggestions('i1')) // allowed again once finished
    expect(api.suggestFollowUps).toHaveBeenCalledTimes(2)
  })

  it('surfaces a failed accept', async () => {
    api.updateFollowUp.mockRejectedValue(new Error('API 404'))
    const store = configureStore({ reducer: { followUps: followUpsReducer } })
    await store.dispatch(acceptFollowUp({ id: 'gone' }))
    expect(store.getState().followUps.error).toBe('Couldn’t add follow-up: API 404')
  })
})
