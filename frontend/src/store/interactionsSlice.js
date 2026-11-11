import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import { api } from '../api/client'

const seesAllReps = (state) => ['manager', 'admin'].includes(state.auth?.user?.role)

/**
 * First page for an HCP. Arg: hcpId, or { hcpId, ownerId }. With only an hcpId the
 * current rep filter is kept (e.g. a refresh after a chat turn logged something).
 * Managers/admins also get the rep list for the filter, counted on the server.
 */
export const fetchInteractions = createAsyncThunk('interactions/fetch', async (arg, { getState }) => {
  const { hcpId, ownerId } = typeof arg === 'object' && arg !== null
    ? { ownerId: null, ...arg }
    : { hcpId: arg, ownerId: getState().interactions?.hcpId === arg ? getState().interactions.ownerId : null }
  const showsReps = seesAllReps(getState())
  const [page, reps] = await Promise.all([
    api.listInteractions(hcpId, { ownerId }),
    showsReps ? api.listInteractionReps(hcpId) : Promise.resolve([]),
  ])
  return { ...page, reps, showsReps, hcpId, ownerId }
})

/** Next page, appended. Uses the HCP and rep filter of the list already shown. */
export const fetchMoreInteractions = createAsyncThunk('interactions/fetchMore', async (_, { getState }) => {
  const { hcpId, ownerId, items } = getState().interactions
  return await api.listInteractions(hcpId, { ownerId, offset: items.length })
})

export const submitStructuredInteraction = createAsyncThunk(
  'interactions/submitStructured',
  async (payload) => await api.createInteraction(payload)
)

export const editInteraction = createAsyncThunk(
  'interactions/edit',
  async ({ id, updates }) => await api.updateInteraction(id, updates)
)

const interactionsSlice = createSlice({
  name: 'interactions',
  initialState: {
    items: [],
    total: 0,
    reps: [], // [{ owner_id, email, count }] for managers/admins
    showsReps: false,
    hcpId: null,
    ownerId: null,
    status: 'idle',
    loadingMore: false,
    lastSubmitted: null,
  },
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(fetchInteractions.pending, (state) => { state.status = 'loading' })
      .addCase(fetchInteractions.fulfilled, (state, action) => {
        const { items, total, reps, showsReps, hcpId, ownerId } = action.payload
        state.status = 'succeeded'
        state.showsReps = showsReps
        state.items = items
        state.total = total
        state.reps = reps
        state.hcpId = hcpId ?? null
        state.ownerId = ownerId ?? null
      })
      .addCase(fetchInteractions.rejected, (state) => { state.status = 'failed' })
      .addCase(fetchMoreInteractions.pending, (state) => { state.loadingMore = true })
      .addCase(fetchMoreInteractions.fulfilled, (state, action) => {
        state.loadingMore = false
        const seen = new Set(state.items.map((i) => i.id))
        state.items.push(...action.payload.items.filter((i) => !seen.has(i.id)))
        state.total = action.payload.total
      })
      .addCase(fetchMoreInteractions.rejected, (state) => { state.loadingMore = false })
      .addCase(submitStructuredInteraction.fulfilled, (state, action) => {
        const item = action.payload
        state.lastSubmitted = item
        // Only show it if it belongs in the list on screen (same HCP, and the rep filter matches).
        if ((state.hcpId && item.hcp_id !== state.hcpId) || (state.ownerId && item.owner_id !== state.ownerId)) return
        state.items.unshift(item)
        state.total += 1
        const rep = state.reps.find((r) => r.owner_id === item.owner_id)
        if (rep) rep.count += 1
        else if (state.showsReps && item.owner_email) {
          state.reps.push({ owner_id: item.owner_id, email: item.owner_email, count: 1 })
          state.reps.sort((a, b) => a.email.localeCompare(b.email))
        }
      })
      .addCase(editInteraction.fulfilled, (state, action) => {
        const idx = state.items.findIndex((i) => i.id === action.payload.id)
        if (idx !== -1) state.items[idx] = action.payload
      })
  },
})

export default interactionsSlice.reducer
