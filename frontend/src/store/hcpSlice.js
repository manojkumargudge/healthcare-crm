import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import { api } from '../api/client'

export const fetchHcps = createAsyncThunk('hcps/fetch', async () => {
  return await api.listHcps()
})

const hcpSlice = createSlice({
  name: 'hcps',
  initialState: {
    items: [],
    selectedId: null,
    status: 'idle',
  },
  reducers: {
    selectHcp(state, action) {
      state.selectedId = action.payload
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchHcps.pending, (state) => { state.status = 'loading' })
      .addCase(fetchHcps.fulfilled, (state, action) => {
        state.status = 'succeeded'
        state.items = action.payload
        if (!state.selectedId && action.payload.length) {
          state.selectedId = action.payload[0].id
        }
      })
      .addCase(fetchHcps.rejected, (state) => { state.status = 'failed' })
  },
})

export const { selectHcp } = hcpSlice.actions
export default hcpSlice.reducer
