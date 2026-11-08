// Shared by playwright.config.js and the specs: how the e2e backend is started.
export const PY = process.env.E2E_PYTHON || 'python'
export const BACKEND_PORT = process.env.E2E_BACKEND_PORT || '8100'
export const FRONTEND_PORT = process.env.E2E_FRONTEND_PORT || '4173'
export const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`
export const GROQ_PORT = process.env.E2E_GROQ_PORT || '9999'
export const E2E_PASSWORD = process.env.E2E_PASSWORD || 'e2e-only-password-not-secret'
export const E2E_DAILY_CAP = 1000 // high enough for the suite; the cap test fills the counter itself

export const backendEnv = {
  DATABASE_URL: process.env.E2E_DATABASE_URL || 'postgresql+asyncpg://hcp_user:hcp_pass@127.0.0.1:5432/hcp_crm_e2e_test',
  JWT_SECRET: process.env.E2E_JWT_SECRET || 'e2e-only-jwt-secret-not-used-anywhere-else-0123456789',
  GROQ_API_KEY: 'fake-e2e-key',
  GROQ_BASE_URL: `http://127.0.0.1:${GROQ_PORT}`,
  FRONTEND_ORIGIN: `http://localhost:${FRONTEND_PORT}`,
  RATE_LIMIT_LLM_PER_MINUTE: '1000',
  LLM_DAILY_CAP: String(E2E_DAILY_CAP),
  DEMO_MODE: process.env.E2E_DEMO_MODE || 'true', // the suite runs like the public demo
  E2E_PASSWORD,
  E2E_BACKEND_PORT: BACKEND_PORT,
}
