import { configureStore } from "@reduxjs/toolkit";
import hcpReducer from "./hcpSlice";
import interactionsReducer from "./interactionsSlice";
import chatReducer from "./chatSlice";
import followUpsReducer from "./followUpsSlice";

export const store = configureStore({
  reducer: {
    hcps: hcpReducer,
    interactions: interactionsReducer,
    chat: chatReducer,
    followUps: followUpsReducer,
  },
});