// End-to-end tests: real backend + real production build, Groq replaced by e2e/support/fake_groq.py.
// Local: E2E_DATABASE_URL must point at a throwaway *_test database (it is wiped).
//        PW_CHANNEL=msedge uses the installed Edge instead of downloading Chromium.
import { defineConfig, devices } from '@playwright/test'
import { PY, BACKEND_URL, FRONTEND_PORT, GROQ_PORT, backendEnv } from './e2e/support/env.js'

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
      url: `${BACKEND_URL}/health`,
      env: backendEnv,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npm run build && npx vite preview --port ${FRONTEND_PORT} --strictPort`,
      url: `http://localhost:${FRONTEND_PORT}`,
      env: { VITE_API_URL: BACKEND_URL },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
