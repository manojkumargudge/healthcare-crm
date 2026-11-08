import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import { api } from '../api/client'

// Signed-in rep ("rep" = app user). Token is kept in memory only.
export const login = createAsyncThunk('auth/login', async ({ email, password }) => api.login(email, password))

const initialState = { token: null, user: null, status: 'idle', error: null, notice: null }

const authSlice = createSlice({
  name: 'auth',
  initialState,
  reducers: {
    logout: () => ({ ...initialState }),
    sessionExpired: () => ({ ...initialState, notice: 'Your session has expired. Please sign in again.' }),
  },
  extraReducers: (builder) => {
    builder
      .addCase(login.pending, (state) => { state.status = 'loading'; state.error = null; state.notice = null })
      .addCase(login.fulfilled, (state, action) => {
        state.status = 'succeeded'
        state.token = action.payload.access_token
        state.user = action.payload.user
      })
      .addCase(login.rejected, (state, action) => {
        state.status = 'failed'
        state.error = action.error.message?.includes('401')
          ? 'Incorrect email or password.'
          : action.error.message
      })
  },
})

export const { logout, sessionExpired } = authSlice.actions
export default authSlice.reducer
