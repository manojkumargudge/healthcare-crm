// @vitest-environment jsdom
// Component tests for the brief gaps: sentiment radio, attendees/materials pickers,
// AI suggested follow-ups, and null sentiment_score display ("—").
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react'
import { Provider } from 'react-redux'
import { configureStore } from '@reduxjs/toolkit'

vi.mock('../api/client', () => ({
  api: {
    listHcps: vi.fn(),
    listInteractions: vi.fn(),
    createInteraction: vi.fn(),
    updateInteraction: vi.fn(),
    chatTurn: vi.fn(),
    searchHcps: vi.fn(),
    searchMaterials: vi.fn(),
    createMaterial: vi.fn(),
    listFollowUps: vi.fn(),
    suggestFollowUps: vi.fn(),
    updateFollowUp: vi.fn(),
  },
}))

import { api } from '../api/client'
import hcpReducer from '../store/hcpSlice'
import interactionsReducer from '../store/interactionsSlice'
import chatReducer from '../store/chatSlice'
import followUpsReducer from '../store/followUpsSlice'
import StructuredForm from './StructuredForm'
import SearchableMultiSelect from './SearchableMultiSelect'
import FollowUpSuggestions from './FollowUpSuggestions'
import PulseStrip from './PulseStrip'
import InteractionHistory from './InteractionHistory'
import { formatScore } from '../lib/format'

const makeStore = () =>
  configureStore({
    reducer: { hcps: hcpReducer, interactions: interactionsReducer, chat: chatReducer, followUps: followUpsReducer },
    preloadedState: { hcps: { items: [{ id: 'h1', name: 'Dr. A' }], selectedId: 'h1', status: 'succeeded' } },
  })

const renderWithStore = (ui, store = makeStore()) => ({ store, ...render(<Provider store={store}>{ui}</Provider>) })

beforeEach(() => {
  vi.clearAllMocks()
  api.searchHcps.mockResolvedValue([{ id: 'h2', name: 'Dr. Priya Nair' }, { id: 'h3', name: 'Dr. Raj Patel' }])
  api.searchMaterials.mockResolvedValue([{ id: 'm1', name: 'CardioMax dosing guide' }])
  api.createMaterial.mockImplementation(async (name) => ({ id: 'm9', name: name.trim() }))
  api.suggestFollowUps.mockResolvedValue([])
  api.listFollowUps.mockResolvedValue([])
})
afterEach(cleanup)

describe('formatScore', () => {
  it('shows an em dash for a missing score and a signed value otherwise', () => {
    expect(formatScore(null)).toBe('—')
    expect(formatScore(undefined)).toBe('—')
    expect(formatScore(0.6)).toBe('+0.60')
    expect(formatScore(-0.25)).toBe('-0.25')
    expect(formatScore(0)).toBe('0.00')
  })
})

describe('null sentiment_score in the UI', () => {
  const rows = [
    { id: 'i1', interaction_type: 'Visit', interaction_date: '2026-11-01', hcp_sentiment: 'Neutral', sentiment_score: null, edit_history: [] },
    { id: 'i2', interaction_type: 'Call', interaction_date: '2026-11-02', hcp_sentiment: 'Positive', sentiment_score: 0.7, edit_history: [] },
  ]

  it('PulseStrip marks unscored bars and shows "—" instead of inventing a score', () => {
    render(<PulseStrip interactions={rows} />)
    const bars = screen.getAllByTestId('pulse-bar')
    const unscored = bars.find((b) => b.title.includes('2026-11-01'))
    expect(unscored.className).toContain('unscored')
    expect(unscored.title).toContain('score —')
    expect(bars.find((b) => b.title.includes('2026-11-02')).title).toContain('score +0.70')
  })

  it('InteractionHistory shows "Score —" for a null score', () => {
    const store = makeStore()
    store.dispatch({ type: 'interactions/fetch/fulfilled', payload: rows })
    renderWithStore(<InteractionHistory />, store)
    expect(screen.getByText('Score —')).toBeTruthy()
    expect(screen.getByText('Score +0.70')).toBeTruthy()
  })
})

describe('SearchableMultiSelect', () => {
  it('searches, adds a suggestion on click, hides already-selected options', async () => {
    let values = []
    const onChange = vi.fn((v) => { values = v })
    const search = vi.fn(async () => ['Dr. Priya Nair', 'Dr. Raj Patel'])
    const { rerender } = render(<SearchableMultiSelect id="att" values={values} onChange={onChange} search={search} debounceMs={0} />)
    const input = screen.getByRole('combobox')
    fireEvent.change(input, { target: { value: 'dr' } })
    await waitFor(() => expect(search).toHaveBeenCalledWith('dr'))
    fireEvent.mouseDown(await screen.findByRole('option', { name: 'Dr. Priya Nair' }))
    expect(onChange).toHaveBeenLastCalledWith(['Dr. Priya Nair'])

    rerender(<SearchableMultiSelect id="att" values={values} onChange={onChange} search={search} debounceMs={0} />)
    fireEvent.change(input, { target: { value: 'd' } })
    await waitFor(() => expect(screen.queryByRole('option', { name: 'Dr. Raj Patel' })).toBeTruthy())
    expect(screen.queryByRole('option', { name: 'Dr. Priya Nair' })).toBeNull()
  })

  it('Enter on unmatched text creates it via onCreate and adds the canonical name', async () => {
    const onChange = vi.fn()
    const onCreate = vi.fn(async (n) => `${n} (catalog)`)
    render(
      <SearchableMultiSelect id="mat" values={[]} onChange={onChange} search={async () => []} onCreate={onCreate} debounceMs={0} />
    )
    const input = screen.getByRole('combobox')
    fireEvent.change(input, { target: { value: 'New leave-behind' } })
    await screen.findByRole('option', { name: 'Add "New leave-behind"' })
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }) })
    expect(onCreate).toHaveBeenCalledWith('New leave-behind')
    expect(onChange).toHaveBeenCalledWith(['New leave-behind (catalog)'])
  })

  it('closes the menu on a click outside the picker', async () => {
    render(
      <div>
        <SearchableMultiSelect id="o" values={[]} onChange={vi.fn()} search={async () => ['Alpha']} debounceMs={0} />
        <p>elsewhere</p>
      </div>
    )
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'a' } })
    await screen.findByRole('option', { name: 'Alpha' })
    fireEvent.mouseDown(screen.getByText('elsewhere'))
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
  })

  it('does not offer "Add" when free text is disallowed', async () => {
    render(<SearchableMultiSelect id="x" values={[]} onChange={vi.fn()} search={async () => []} allowCreate={false} debounceMs={0} />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'zzz' } })
    await new Promise((r) => setTimeout(r, 10))
    expect(screen.queryByRole('option')).toBeNull()
  })
})

describe('StructuredForm', () => {
  it('sends the optional sentiment radio, attendees and catalog materials', async () => {
    api.createInteraction.mockResolvedValue({ id: 'new-1', compliance_flag: false })
    renderWithStore(<StructuredForm />)

    // sentiment radio: optional, selectable, clearable
    fireEvent.click(screen.getByLabelText('Positive'))
    fireEvent.click(screen.getByText('Clear'))
    expect(screen.getByLabelText('Positive').checked).toBe(false)
    fireEvent.click(screen.getByLabelText('Negative'))

    // attendees from HCP search
    const attendees = screen.getByLabelText('Attendees')
    fireEvent.change(attendees, { target: { value: 'priya' } })
    fireEvent.mouseDown(await screen.findByRole('option', { name: 'Dr. Priya Nair' }))
    expect(api.searchHcps).toHaveBeenCalledWith('priya')

    // materials: pick from catalog search
    const materials = screen.getByLabelText('Materials shared')
    fireEvent.change(materials, { target: { value: 'dosing' } })
    fireEvent.mouseDown(await screen.findByRole('option', { name: 'CardioMax dosing guide' }))

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Log interaction' })) })
    expect(api.createInteraction).toHaveBeenCalledWith(
      expect.objectContaining({
        hcp_id: 'h1',
        hcp_sentiment: 'Negative',
        attendees: ['Dr. Priya Nair'],
        materials_shared: ['CardioMax dosing guide'],
      })
    )
    // follow-up suggestions are requested for the new interaction
    await waitFor(() => expect(api.suggestFollowUps).toHaveBeenCalledWith('new-1'))
  })

  it('sends hcp_sentiment null when no radio is chosen', async () => {
    api.createInteraction.mockResolvedValue({ id: 'new-2', compliance_flag: false })
    renderWithStore(<StructuredForm />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Log interaction' })) })
    expect(api.createInteraction).toHaveBeenCalledWith(expect.objectContaining({ hcp_sentiment: null, attendees: [] }))
  })
})

describe('FollowUpSuggestions', () => {
  it('lists AI suggestions; clicking one adds it (status Pending)', async () => {
    const suggestion = { id: 'f1', interaction_id: 'i1', reason: 'Send OncoBoost Phase III PDF', due_date: '2026-11-10', status: 'Suggested' }
    api.listFollowUps.mockResolvedValue([suggestion])
    api.updateFollowUp.mockResolvedValue({ ...suggestion, status: 'Pending' })
    renderWithStore(<FollowUpSuggestions interactionId="i1" />)

    expect(screen.getByRole('heading', { name: 'AI Suggested Follow-ups' })).toBeTruthy()
    const add = await screen.findByTitle('Add this follow-up')
    expect(add.textContent).toContain('Send OncoBoost Phase III PDF')
    await act(async () => { fireEvent.click(add) })
    expect(api.updateFollowUp).toHaveBeenCalledWith('f1', { status: 'Pending' })
    await waitFor(() => expect(screen.queryByTitle('Add this follow-up')).toBeNull())
    expect(screen.getByText('✓')).toBeTruthy()
  })

  it('shows an error when suggestions fail', async () => {
    api.suggestFollowUps.mockRejectedValue(new Error('API 502: groq down'))
    renderWithStore(<FollowUpSuggestions interactionId="i2" />)
    expect(await screen.findByText(/Couldn’t get suggestions: API 502/)).toBeTruthy()
  })
})
