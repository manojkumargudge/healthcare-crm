// @vitest-environment jsdom
// "Log Interaction" must stay disabled while a new material is still being created,
// and a failed create must be reported (not silently dropped).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { Provider } from 'react-redux'
import { configureStore } from '@reduxjs/toolkit'

vi.mock('../api/client', () => ({
  api: {
    searchHcps: vi.fn(),
    searchMaterials: vi.fn(),
    createMaterial: vi.fn(),
    searchProducts: vi.fn(),
    createInteraction: vi.fn(),
    listFollowUps: vi.fn(),
    suggestFollowUps: vi.fn(),
  },
}))

import { api } from '../api/client'
import hcpReducer from '../store/hcpSlice'
import interactionsReducer from '../store/interactionsSlice'
import followUpsReducer from '../store/followUpsSlice'
import StructuredForm from './StructuredForm'

const renderForm = () => {
  const store = configureStore({
    reducer: { hcps: hcpReducer, interactions: interactionsReducer, followUps: followUpsReducer },
    preloadedState: { hcps: { items: [{ id: 'h1', name: 'Dr. A' }], selectedId: 'h1', status: 'succeeded' } },
  })
  return render(<Provider store={store}><StructuredForm /></Provider>)
}

const typeNewMaterial = async (name) => {
  fireEvent.click(screen.getByRole('button', { name: /Search\/Add/ }))
  const input = screen.getByLabelText('Materials Shared')
  fireEvent.change(input, { target: { value: name } })
  await screen.findByRole('option', { name: new RegExp(`Add "${name}"`) })
  return input
}

beforeEach(() => {
  vi.clearAllMocks()
  api.searchMaterials.mockResolvedValue([])
  api.searchProducts.mockResolvedValue([])
  api.searchHcps.mockResolvedValue([])
})
afterEach(cleanup)

describe('material save race', () => {
  it('disables Log Interaction until the new material is saved', async () => {
    let finish
    api.createMaterial.mockImplementation((name) => new Promise((r) => { finish = () => r({ id: 'm9', name, approved: false }) }))
    renderForm()
    const input = await typeNewMaterial('Leaflet X')
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }) })

    const button = screen.getByRole('button', { name: 'Saving material…' })
    expect(button.disabled).toBe(true)
    expect(screen.queryByRole('button', { name: 'Log Interaction' })).toBeNull()
    // A second Enter while saving must not create a duplicate.
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }) })
    expect(api.createMaterial).toHaveBeenCalledTimes(1)

    await act(async () => { finish() })
    expect(screen.getByRole('button', { name: 'Log Interaction' }).disabled).toBe(false)
    expect(screen.getByRole('button', { name: 'Remove Leaflet X' })).not.toBeNull()
  })

  it('shows an error and re-enables submit when creating the material fails', async () => {
    api.createMaterial.mockRejectedValue(new Error('Server error 500'))
    renderForm()
    const input = await typeNewMaterial('Leaflet Y')
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }) })

    expect(screen.getByRole('alert').textContent).toContain('Couldn’t add "Leaflet Y": Server error 500')
    expect(screen.getByRole('button', { name: 'Log Interaction' }).disabled).toBe(false)
    expect(screen.queryByRole('button', { name: 'Remove Leaflet Y' })).toBeNull()
  })
})
