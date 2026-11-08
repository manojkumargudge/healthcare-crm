// End-to-end: real UI + real backend + real database; only Groq is faked (e2e/support/fake_groq.py).
import { test, expect } from '@playwright/test'

const PASSWORD = process.env.E2E_PASSWORD || 'e2e-only-password-not-secret'
const USERS = {
  admin: 'e2e-admin@example.com',
  manager: 'e2e-manager@example.com',
  rep: 'e2e-rep@example.com',
}
const stamp = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`

async function signIn(page, who, password = PASSWORD) {
  await page.goto('/')
  await page.getByLabel('Email').fill(USERS[who])
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

async function signedIn(page, who) {
  await signIn(page, who)
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  await expect(page.locator('#topics')).toBeVisible()
}

async function signOut(page) {
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('form', { name: 'Sign in' })).toBeVisible()
}

test.describe('sign-in per role', () => {
  for (const [who, seesAdmin] of [['rep', false], ['manager', false], ['admin', true]]) {
    test(`${who} signs in and ${seesAdmin ? 'sees' : 'does not see'} the Admin screen`, async ({ page }) => {
      await signedIn(page, who)
      await expect(page.getByText(USERS[who])).toBeVisible()
      await expect(page.getByRole('button', { name: 'Admin' })).toHaveCount(seesAdmin ? 1 : 0)
      await signOut(page)
    })
  }

  test('wrong password is rejected', async ({ page }) => {
    await signIn(page, 'rep', 'not-the-password')
    await expect(page.getByRole('alert')).toHaveText('Incorrect email or password.')
  })
})

test('rep logs via the form, then edits it', async ({ page }) => {
  const id = stamp()
  await signedIn(page, 'rep')
  await page.locator('#topics').fill(`E2E form note ${id}`)
  await page.getByRole('button', { name: 'Log Interaction' }).click()
  await expect(page.locator('.form-success')).toContainText('Interaction logged')
  const row = page.locator('.history-item', { hasText: `E2E form note ${id}` })
  await expect(row).toBeVisible()

  await row.getByRole('button', { name: 'Edit Interaction' }).click()
  await page.getByLabel('Edit notes').fill(`E2E edited ${id}`)
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.locator('.history-item', { hasText: `E2E edited ${id}` })).toBeVisible()
  await expect(page.getByText(`E2E form note ${id}`)).toHaveCount(0)
})

test('rep logs via chat (real agent, fake model)', async ({ page }) => {
  const id = stamp()
  await signedIn(page, 'rep')
  await page.getByPlaceholder('Describe interaction...').fill(`Please log a call: discussed CardioMax pricing ${id}`)
  await page.locator('.chat-footer').getByRole('button', { name: 'Log' }).click()
  await expect(page.locator('.chat-bubble.assistant', { hasText: 'Logged it.' })).toBeVisible()
  const row = page.locator('.history-item', { hasText: id })
  await expect(row).toBeVisible()
  await expect(row).toContainText('E2E chat summary')
  await expect(row).toContainText('CardioMax')
})

test('voice note: recording is blocked until consent, then the summary fills Topics', async ({ page }) => {
  await signedIn(page, 'rep')
  await page.getByRole('button', { name: /Summarize from Voice Note \(Requires Consent\)/ }).click()
  const record = page.getByRole('button', { name: /Record/ })
  await expect(record).toBeDisabled()
  await page.getByRole('checkbox', { name: /HCP consents to this recording/ }).check()
  await expect(record).toBeEnabled()
  await record.click()
  await expect(page.getByText('Recording…')).toBeVisible()
  await page.waitForTimeout(1500)
  await page.getByRole('button', { name: /Stop/ }).click()
  await expect(page.getByText('Summary added to Topics Discussed.')).toBeVisible()
  await expect(page.locator('#topics')).toHaveValue(/E2E summary: discussed CardioMax pricing/)
  await expect(page.getByText(/Voice note consent recorded/)).toBeVisible()
})

test('material approval: a rep-added material is flagged until an admin approves it', async ({ page }) => {
  const material = `E2E leaflet ${stamp()}`
  await signedIn(page, 'rep')
  await page.getByRole('button', { name: /Search\/Add/ }).click()
  await page.locator('#materials').fill(material)
  await page.getByRole('option', { name: `Add "${material}" to materials` }).click()
  await page.getByRole('button', { name: 'Log Interaction' }).click()
  await expect(page.locator('.form-success')).toContainText(`Materials pending approval: ${material}`)
  await expect(page.locator('.compliance-flag', { hasText: material })).toBeVisible()
  await signOut(page)

  await signedIn(page, 'admin')
  await page.getByRole('button', { name: 'Admin' }).click()
  await page.locator('.admin-list li', { hasText: material }).getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByRole('status')).toContainText('1 compliance flag(s) cleared')
  await signOut(page)

  await signedIn(page, 'rep')
  await expect(page.locator('.history-item', { hasText: material })).toBeVisible()
  await expect(page.locator('.compliance-flag', { hasText: material })).toHaveCount(0)
})
