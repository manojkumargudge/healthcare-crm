import { createSlice, createAsyncThunk, nanoid } from '@reduxjs/toolkit'
import { api } from '../api/client'

export const sendChatMessage = createAsyncThunk(
  'chat/sendMessage',
  async ({ sessionId, message, hcpId }) => {
    return await api.chatTurn({ session_id: sessionId, message, hcp_id: hcpId })
  }
)

const chatSlice = createSlice({
  name: 'chat',
  initialState: {
    sessionId: nanoid(),
    messages: [], // { role: 'user'|'assistant', content, toolCalls? }
    status: 'idle',
  },
  reducers: {
    resetSession(state) {
      state.sessionId = nanoid()
      state.messages = []
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(sendChatMessage.pending, (state, action) => {
        state.status = 'loading'
        state.messages.push({ role: 'user', content: action.meta.arg.message })
      })
      .addCase(sendChatMessage.fulfilled, (state, action) => {
        state.status = 'succeeded'
        state.messages.push({
          role: 'assistant',
          content: action.payload.reply,
          toolCalls: action.payload.tool_calls,
          interaction: action.payload.interaction,
        })
      })
      .addCase(sendChatMessage.rejected, (state, action) => {
        state.status = 'failed'
        state.messages.push({ role: 'assistant', content: `Something went wrong: ${action.error.message}`, error: true })
      })
  },
})

export const { resetSession } = chatSlice.actions
export default chatSlice.reducer
