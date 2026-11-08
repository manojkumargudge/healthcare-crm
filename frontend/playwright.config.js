// End-to-end tests: real backend + real production build, Groq replaced by e2e/support/fake_groq.py.
// Local: E2E_DATABASE_URL must point at a throwaway *_test database (it is wiped).
//        PW_CHANNEL=msedge uses the installed Edge instead of downloading Chromium.
import { defineConfig, devices } from '@playwright/test'

const PY = process.env.E2E_PYTHON || 'python'
const BACKEND_PORT = process.env.E2E_BACKEND_PORT || '8100'
const FRONTEND_PORT = process.env.E2E_FRONTEND_PORT || '4173'
const GROQ_PORT = process.env.E2E_GROQ_PORT || '9999'
export const E2E_PASSWORD = process.env.E2E_PASSWORD || 'e2e-only-password-not-secret'

const backendEnv = {
  DATABASE_URL: process.env.E2E_DATABASE_URL || 'postgresql+asyncpg://hcp_user:hcp_pass@127.0.0.1:5432/hcp_crm_e2e_test',
  JWT_SECRET: process.env.E2E_JWT_SECRET || 'e2e-only-jwt-secret-not-used-anywhere-else-0123456789',
  GROQ_API_KEY: 'fake-e2e-key',
  GROQ_BASE_URL: `http://127.0.0.1:${GROQ_PORT}`,
  FRONTEND_ORIGIN: `http://localhost:${FRONTEND_PORT}`,
  RATE_LIMIT_LLM_PER_MINUTE: '1000',
  E2E_PASSWORD,
  E2E_BACKEND_PORT: BACKEND_PORT,
}

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1, // one shared database
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    permissions: ['microphone'],
    launchOptions: {
      // Fake microphone for the voice-note test.
      args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
    },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}) },
    },
  ],
  webServer: [
    {
      command: `"${PY}" e2e/support/fake_groq.py ${GROQ_PORT}`,
      url: `http://127.0.0.1:${GROQ_PORT}/`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `"${PY}" e2e/support/run_backend.py`,
      url: `http://127.0.0.1:${BACKEND_PORT}/health`,
      env: backendEnv,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npm run build && npx vite preview --port ${FRONTEND_PORT} --strictPort`,
      url: `http://localhost:${FRONTEND_PORT}`,
      env: { VITE_API_URL: `http://127.0.0.1:${BACKEND_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
