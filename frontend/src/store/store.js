import { configureStore } from "@reduxjs/toolkit";
import hcpReducer from "./hcpSlice";
import interactionsReducer from "./interactionsSlice";
import chatReducer from "./chatSlice";

export const store = configureStore({
  reducer: {
    hcps: hcpReducer,
    interactions: interactionsReducer,
    chat: chatReducer,
  },
});