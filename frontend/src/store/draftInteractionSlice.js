import { createSlice } from '@reduxjs/toolkit'

const initialState = {
  interaction_type: 'Visit',
  interaction_date: new Date().toISOString().slice(0, 10),
  products_discussed: [],
  key_topics: [],
  materials_shared: [],
  samples_dropped: [],
  raw_notes: '',
  ai_summary: '',
  hcp_sentiment: '',
}

const draftInteractionSlice = createSlice({
  name: 'draftInteraction',

  initialState,

  reducers: {
    setDraftInteraction(state, action) {
      return {
        ...state,
        ...action.payload,
      }
    },

    clearDraftInteraction() {
      return {
        ...initialState,
        interaction_date: new Date().toISOString().slice(0, 10),
      }
    },

    updateDraftField(state, action) {
      const { field, value } = action.payload
      state[field] = value
    },
  },
})

export const {
  setDraftInteraction,
  clearDraftInteraction,
  updateDraftField,
} = draftInteractionSlice.actions

export default draftInteractionSlice.reducer