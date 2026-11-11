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
    searchProducts: vi.fn(),
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
  api.searchMaterials.mockResolvedValue([{ id: 'm1', name: 'CardioMax dosing guide', approved: true }])
  api.createMaterial.mockImplementation(async (name) => ({ id: 'm9', name: name.trim(), approved: false }))
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
    store.dispatch({ type: 'interactions/fetch/fulfilled', payload: { items: rows, total: rows.length, reps: [] } })
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

  it('never offers stale results from an earlier query (Enter adds the typed text)', async () => {
    const onChange = vi.fn()
    // Slow search: results for "card" are still in state while the user types a new name.
    const search = vi.fn(async (q) => (q === 'card' ? ['CardioMax dosing guide'] : new Promise(() => {})))
    render(<SearchableMultiSelect id="s" values={[]} onChange={onChange} search={search} debounceMs={0} />)
    const input = screen.getByRole('combobox')
    fireEvent.change(input, { target: { value: 'card' } })
    await screen.findByRole('option', { name: 'CardioMax dosing guide' })
    fireEvent.change(input, { target: { value: 'New thing' } })
    expect(screen.queryByRole('option', { name: 'CardioMax dosing guide' })).toBeNull()
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }) })
    expect(onChange).toHaveBeenCalledWith(['New thing'])
  })

  it('does not offer "Add" when free text is disallowed', async () => {
    render(<SearchableMultiSelect id="x" values={[]} onChange={vi.fn()} search={async () => []} allowCreate={false} debounceMs={0} />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'zzz' } })
    await new Promise((r) => setTimeout(r, 10))
    expect(screen.queryByRole('option')).toBeNull()
  })
})

describe('StructuredForm', () => {
  it('marks unapproved and newly added materials "Pending approval"', async () => {
    const catalog = [
      { id: 'm1', name: 'CardioMax dosing guide', approved: true },
      { id: 'm2', name: 'Rep slide deck', approved: false },
    ]
    // Like the real API: case-insensitive substring match on the typed text.
    api.searchMaterials.mockImplementation(async (q) => catalog.filter((m) => m.name.toLowerCase().includes(q.toLowerCase())))
    renderWithStore(<StructuredForm />)
    fireEvent.click(screen.getByRole('button', { name: /Search\/Add/ }))
    const materials = screen.getByLabelText('Materials Shared')
    fireEvent.change(materials, { target: { value: 'de' } })
    const pendingOpt = await screen.findByRole('option', { name: /Rep slide deck/ })
    expect(pendingOpt.textContent).toContain('Pending approval')
    expect(screen.getByRole('option', { name: /CardioMax dosing guide/ }).textContent).not.toContain('Pending')

    fireEvent.change(materials, { target: { value: 'Brand new leave-behind' } })
    await screen.findByRole('option', { name: /Add "Brand new leave-behind"/ })
    await act(async () => { fireEvent.keyDown(materials, { key: 'Enter' }) })
    expect(api.createMaterial).toHaveBeenCalledWith('Brand new leave-behind')
    const chip = screen.getByText('Brand new leave-behind').closest('.chip')
    expect(chip.textContent).toContain('Pending approval')
  })

  it('sends the optional sentiment radio, attendees and catalog materials', async () => {
    api.createInteraction.mockResolvedValue({ id: 'new-1', compliance_flag: false })
    renderWithStore(<StructuredForm />)

    // sentiment radio: optional, selectable, clearable
    fireEvent.click(screen.getByLabelText(/Positive/))
    fireEvent.click(screen.getByText('Clear'))
    expect(screen.getByLabelText(/Positive/).checked).toBe(false)
    fireEvent.click(screen.getByLabelText(/Negative/))

    // attendees from HCP search
    const attendees = screen.getByLabelText('Attendees')
    fireEvent.change(attendees, { target: { value: 'priya' } })
    fireEvent.mouseDown(await screen.findByRole('option', { name: 'Dr. Priya Nair' }))
    expect(api.searchHcps).toHaveBeenCalledWith('priya')

    // materials: pick from catalog search
    fireEvent.click(screen.getByRole('button', { name: /Search\/Add/ }))
    const materials = screen.getByLabelText('Materials Shared')
    fireEvent.change(materials, { target: { value: 'dosing' } })
    fireEvent.mouseDown(await screen.findByRole('option', { name: 'CardioMax dosing guide' }))

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Log Interaction' })) })
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
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Log Interaction' })) })
    expect(api.createInteraction).toHaveBeenCalledWith(expect.objectContaining({ hcp_sentiment: null, attendees: [], interaction_type: 'Meeting' }))
  })
})

describe('FollowUpSuggestions', () => {
  it('lists AI suggestions; clicking one adds it (status Pending)', async () => {
    const suggestion = { id: 'f1', interaction_id: 'i1', reason: 'Send OncoBoost Phase III PDF', due_date: '2026-11-10', status: 'Suggested' }
    api.listFollowUps.mockResolvedValue([suggestion])
    api.updateFollowUp.mockResolvedValue({ ...suggestion, status: 'Pending' })
    renderWithStore(<FollowUpSuggestions interactionId="i1" />)

    expect(screen.getByRole('heading', { name: 'AI Suggested Follow-ups:' })).toBeTruthy()
    const add = await screen.findByTitle(/^Add this follow-up/)
    expect(add.textContent).toContain('Send OncoBoost Phase III PDF')
    await act(async () => { fireEvent.click(add) })
    expect(api.updateFollowUp).toHaveBeenCalledWith('f1', { status: 'Pending' })
    await waitFor(() => expect(screen.queryByTitle(/^Add this follow-up/)).toBeNull())
    expect(screen.getByText('✓')).toBeTruthy()
  })

  it('shows an error when suggestions fail', async () => {
    api.suggestFollowUps.mockRejectedValue(new Error('API 502: groq down'))
    renderWithStore(<FollowUpSuggestions interactionId="i2" />)
    expect(await screen.findByText(/Couldn’t get suggestions: API 502/)).toBeTruthy()
  })
})

describe('hcpDisplayName', () => {
  it('adds "Dr." only when the name does not already have it', async () => {
    const { hcpDisplayName } = await import('../lib/format')
    expect(hcpDisplayName('Dr. Anjali Rao')).toBe('Dr. Anjali Rao')
    expect(hcpDisplayName('dr Anjali Rao')).toBe('dr Anjali Rao')
    expect(hcpDisplayName('Anjali Rao')).toBe('Dr. Anjali Rao')
    expect(hcpDisplayName('')).toBe('')
  })
})

describe('StructuredForm matches the brief mockup', () => {
  it('has the mockup fields, labels and empty states', () => {
    renderWithStore(<StructuredForm />)
    expect(screen.getByText('Interaction Details')).toBeTruthy()
    for (const label of ['HCP Name', 'Interaction Type', 'Date', 'Time', 'Attendees', 'Topics Discussed', 'Outcomes', 'Follow-up Actions']) {
      expect(screen.getByLabelText(label)).toBeTruthy()
    }
    expect(screen.getByText('Materials Shared / Samples Distributed')).toBeTruthy()
    expect(screen.getByText('No materials added')).toBeTruthy()
    expect(screen.getByText('No samples added')).toBeTruthy()
    expect(screen.getByText('Observed/Inferred HCP Sentiment')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'AI Suggested Follow-ups:' })).toBeTruthy()
    expect(screen.getByText(/Suggestions appear here after you log/)).toBeTruthy()
    expect(screen.getByPlaceholderText('Search or select HCP...')).toBeTruthy()
    expect(screen.getByPlaceholderText('Enter names or search...')).toBeTruthy()
    expect(screen.getByPlaceholderText('Enter key discussion points...')).toBeTruthy()
    expect(screen.getByPlaceholderText('Key outcomes or agreements...')).toBeTruthy()
    expect(screen.getByPlaceholderText('Enter next steps or tasks...')).toBeTruthy()
    expect(screen.getByLabelText('Interaction Type').value).toBe('Meeting')
  })

  it('sends time, topics (as notes), outcomes and follow-up actions', async () => {
    api.createInteraction.mockResolvedValue({ id: 'n3', compliance_flag: false })
    renderWithStore(<StructuredForm />)
    fireEvent.change(screen.getByLabelText('Time'), { target: { value: '19:36' } })
    fireEvent.change(screen.getByLabelText('Topics Discussed'), { target: { value: 'Efficacy data' } })
    fireEvent.change(screen.getByLabelText('Outcomes'), { target: { value: 'Agreed to trial' } })
    fireEvent.change(screen.getByLabelText('Follow-up Actions'), { target: { value: 'Send PDF' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Log Interaction' })) })
    expect(api.createInteraction).toHaveBeenCalledWith(expect.objectContaining({
      interaction_time: '19:36', raw_notes: 'Efficacy data', outcomes: 'Agreed to trial', follow_up_actions: 'Send PDF',
    }))
  })

  it('Add Sample opens the sample row and lists added samples', async () => {
    api.searchProducts.mockResolvedValue([{ id: 'p1', name: 'CardioMax' }])
    renderWithStore(<StructuredForm />)
    fireEvent.click(screen.getByRole('button', { name: /Add Sample/ }))
    fireEvent.change(screen.getByLabelText('Sample product'), { target: { value: 'CardioMax' } })
    fireEvent.change(screen.getByLabelText('Sample quantity'), { target: { value: '2' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Add' })) })
    expect(screen.getByText('CardioMax × 2')).toBeTruthy()
    expect(screen.queryByText('No samples added')).toBeNull()
  })

  it('HCP Name picker selects the HCP for the form', () => {
    const store = configureStore({
      reducer: { hcps: hcpReducer, interactions: interactionsReducer, chat: chatReducer, followUps: followUpsReducer },
      preloadedState: { hcps: { items: [{ id: 'h1', name: 'Dr. A' }, { id: 'h2', name: 'Dr. Priya Nair', specialty: 'Oncologist' }], selectedId: 'h1', status: 'succeeded' } },
    })
    renderWithStore(<StructuredForm />, store)
    const input = screen.getByLabelText('HCP Name')
    expect(input.value).toBe('Dr. A')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'priya' } })
    fireEvent.mouseDown(screen.getByRole('option', { name: /Dr. Priya Nair/ }))
    expect(store.getState().hcps.selectedId).toBe('h2')
    expect(input.value).toBe('Dr. Priya Nair')
  })
})
