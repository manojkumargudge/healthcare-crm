import { combineReducers, configureStore } from "@reduxjs/toolkit";
import hcpReducer from "./hcpSlice";
import interactionsReducer from "./interactionsSlice";
import chatReducer from "./chatSlice";
import followUpsReducer from "./followUpsSlice";
import authReducer, { sessionExpired } from "./authSlice";
import { setAuthToken, setUnauthorizedHandler } from "../api/client";

const appReducer = combineReducers({
  auth: authReducer,
  hcps: hcpReducer,
  interactions: interactionsReducer,
  chat: chatReducer,
  followUps: followUpsReducer,
});

// Signing out (or an expired session) clears every slice, so nothing from one
// rep's session is visible to the next person who signs in on this browser.
export const rootReducer = (state, action) => {
  if (action.type === "auth/logout" || action.type === "auth/sessionExpired") {
    return appReducer({ auth: authReducer(undefined, action) }, action);
  }
  return appReducer(state, action);
};

export function createAppStore(preloadedState) {
  const s = configureStore({ reducer: rootReducer, preloadedState });
  // Keep the API client's in-memory token in sync; any 401 ends the session.
  let last;
  s.subscribe(() => {
    const token = s.getState().auth.token;
    if (token !== last) {
      last = token;
      setAuthToken(token);
    }
  });
  setUnauthorizedHandler(() => {
    if (s.getState().auth.token) s.dispatch(sessionExpired());
  });
  return s;
}

export const store = createAppStore();
