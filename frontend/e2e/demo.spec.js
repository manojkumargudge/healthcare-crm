// Demo hardening, end to end (backend runs with DEMO_MODE=true, see e2e/support/env.js):
// banner + read-only user management, the global daily AI cap, and the Rep column/filter.
import { execFileSync } from 'node:child_process'
import { test, expect } from '@playwright/test'
import { PY, BACKEND_URL, backendEnv, E2E_DAILY_CAP, E2E_PASSWORD } from './support/env.js'

const USERS = {
  admin: 'e2e-admin@example.com',
  manager: 'e2e-manager@example.com',
  rep: 'e2e-rep@example.com',
}
const stamp = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`

function setTodaysAiCalls(calls) {
  execFileSync(PY, ['e2e/support/set_llm_usage.py', String(calls)], {
    env: { ...process.env, DATABASE_URL: backendEnv.DATABASE_URL },
    stdio: 'pipe',
  })
}

async function signIn(page, who) {
  await page.goto('/')
  await page.getByLabel('Email').fill(USERS[who])
  await page.getByLabel('Password').fill(E2E_PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  await expect(page.locator('#topics')).toBeVisible()
}

async function signOut(page) {
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('form', { name: 'Sign in' })).toBeVisible()
}

test.describe('demo mode', () => {
  test('banner shows on sign-in and in the app', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByRole('note', { name: 'Demo mode' })).toContainText('resets nightly')
    await signIn(page, 'rep')
    await expect(page.getByRole('note', { name: 'Demo mode' })).toBeVisible()
  })

  test('admin sees user management as read-only, and the API refuses changes', async ({ page }) => {
    await signIn(page, 'admin')
    await page.getByRole('button', { name: 'Admin' }).click()
    const reps = page.getByRole('region', { name: 'Reps' })
    await expect(reps.getByText(USERS.rep)).toBeVisible()
    await expect(reps.getByTestId('users-read-only')).toContainText('read-only')
    await expect(reps.getByRole('button', { name: 'Add rep' })).toHaveCount(0)
    await expect(reps.getByRole('button', { name: /Deactivate|Reset password/ })).toHaveCount(0)

    // Even a hand-made request is refused by the server.
    const api = BACKEND_URL
    const login = await page.request.post(`${api}/auth/login`, {
      form: { username: USERS.admin, password: E2E_PASSWORD },
    })
    const { access_token: token } = await login.json()
    const users = await (await page.request.get(`${api}/api/admin/reps`, { headers: { Authorization: `Bearer ${token}` } })).json()
    const rep = users.find((u) => u.email === USERS.rep)
    const res = await page.request.patch(`${api}/api/admin/reps/${rep.id}`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { is_active: false },
    })
    expect(res.status()).toBe(403)
  })
})

test.describe('daily AI cap', () => {
  test.afterEach(() => setTodaysAiCalls(0))

  test('when the cap is reached chat explains it, and the form still works', async ({ page }) => {
    await signIn(page, 'rep')
    setTodaysAiCalls(E2E_DAILY_CAP)
    await page.getByPlaceholder('Describe interaction...').fill('Please log a call about CardioMax')
    await page.locator('.chat-footer').getByRole('button', { name: 'Log' }).click()
    const bubble = page.locator('.chat-bubble.assistant', { hasText: 'daily limit' })
    await expect(bubble).toContainText(`The daily limit of ${E2E_DAILY_CAP} AI requests has been reached`)
    await expect(bubble).toContainText('you can still log interactions with the form')
    await expect(bubble).not.toContainText('Something went wrong')

    const id = stamp()
    await page.locator('#topics').fill(`E2E capped form note ${id}`)
    await page.getByRole('button', { name: 'Log Interaction' }).click()
    await expect(page.locator('.form-success')).toContainText('Interaction logged')
    await expect(page.locator('.history-item', { hasText: id })).toBeVisible()
  })
})

test('managers see the Rep on each interaction and can filter by rep', async ({ page }) => {
  const id = stamp()
  await signIn(page, 'rep')
  await expect(page.getByLabel('Rep', { exact: true })).toHaveCount(0) // reps don't get the filter
  await page.locator('#topics').fill(`E2E rep-filter note ${id}`)
  await page.getByRole('button', { name: 'Log Interaction' }).click()
  await expect(page.locator('.form-success')).toContainText('Interaction logged')
  await signOut(page)

  await signIn(page, 'manager')
  const row = page.locator('.history-item', { hasText: id })
  await expect(row).toContainText(`Rep: ${USERS.rep}`)
  const filter = page.getByLabel('Rep', { exact: true })
  await filter.selectOption(USERS.rep)
  await expect(row).toBeVisible()
  await expect(page.locator('.history-rep').filter({ hasNotText: USERS.rep })).toHaveCount(0)
  await filter.selectOption({ index: 0 }) // All reps
  await expect(row).toBeVisible()
})
