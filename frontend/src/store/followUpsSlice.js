import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import { api } from '../api/client'

// Follow-ups per interaction: AI suggestions (status "Suggested") and ones the
// rep accepted ("Pending") or the agent scheduled.

export const fetchFollowUps = createAsyncThunk('followUps/fetch', async (interactionId) => ({
  interactionId,
  items: await api.listFollowUps(interactionId),
}))

export const generateSuggestions = createAsyncThunk(
  'followUps/suggest',
  async (interactionId) => {
    await api.suggestFollowUps(interactionId)
    return { interactionId, items: await api.listFollowUps(interactionId) }
  },
  {
    // One generation per interaction at a time (React StrictMode runs effects
    // twice in development; users double-click).
    condition: (interactionId, { getState }) => !getState().followUps?.inFlight?.[interactionId],
  }
)

export const acceptFollowUp = createAsyncThunk('followUps/accept', async ({ id }) =>
  api.updateFollowUp(id, { status: 'Pending' })
)

export const dismissFollowUp = createAsyncThunk('followUps/dismiss', async ({ id }) =>
  api.updateFollowUp(id, { status: 'Dismissed' })
)

const followUpsSlice = createSlice({
  name: 'followUps',
  initialState: { byInteraction: {}, inFlight: {}, status: 'idle', error: null },
  reducers: {},
  extraReducers: (builder) => {
    const replace = (state, action) => {
      state.status = 'succeeded'
      state.error = null
      state.byInteraction[action.payload.interactionId] = action.payload.items
    }
    const upsert = (state, action) => {
      const fu = action.payload
      const list = state.byInteraction[fu.interaction_id] || []
      const i = list.findIndex((x) => x.id === fu.id)
      if (i === -1) list.push(fu)
      else list[i] = fu
      state.byInteraction[fu.interaction_id] = list
    }
    builder
      .addCase(fetchFollowUps.fulfilled, replace)
      .addCase(generateSuggestions.pending, (state, action) => {
        state.status = 'loading'
        state.error = null
        state.inFlight[action.meta.arg] = true
      })
      .addCase(generateSuggestions.fulfilled, (state, action) => {
        delete state.inFlight[action.meta.arg]
        replace(state, action)
      })
      .addCase(generateSuggestions.rejected, (state, action) => {
        delete state.inFlight[action.meta.arg]
        state.status = 'failed'
        state.error = action.error.message
      })
      .addCase(acceptFollowUp.fulfilled, upsert)
      .addCase(acceptFollowUp.rejected, (state, action) => {
        state.error = 'Couldn’t add follow-up: ' + action.error.message
      })
      .addCase(dismissFollowUp.fulfilled, upsert)
  },
})

export default followUpsSlice.reducer
