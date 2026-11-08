import { defineConfig } from 'vite'
import { configDefaults } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  test: {
    // e2e/ holds Playwright specs (npm run e2e), not Vitest tests.
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
})
