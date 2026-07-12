import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import { api } from '../api/client'

export const fetchInteractions = createAsyncThunk('interactions/fetch', async (hcpId) => {
  return await api.listInteractions(hcpId)
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
    status: 'idle',
    lastSubmitted: null,
  },
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(fetchInteractions.pending, (state) => { state.status = 'loading' })
      .addCase(fetchInteractions.fulfilled, (state, action) => {
        state.status = 'succeeded'
        state.items = action.payload
      })
      .addCase(submitStructuredInteraction.fulfilled, (state, action) => {
        state.items.unshift(action.payload)
        state.lastSubmitted = action.payload
      })
      .addCase(editInteraction.fulfilled, (state, action) => {
        const idx = state.items.findIndex((i) => i.id === action.payload.id)
        if (idx !== -1) state.items[idx] = action.payload
      })
  },
})

export default interactionsSlice.reducer
