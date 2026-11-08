// @vitest-environment jsdom
// Voice note (consent-gated recording -> summary into Topics Discussed) and Products Discussed.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react'
import { Provider } from 'react-redux'
import { configureStore } from '@reduxjs/toolkit'

vi.mock('../api/client', () => ({
  api: {
    listHcps: vi.fn(), listInteractions: vi.fn(), createInteraction: vi.fn(), updateInteraction: vi.fn(),
    chatTurn: vi.fn(), searchHcps: vi.fn(), searchMaterials: vi.fn(), createMaterial: vi.fn(),
    listFollowUps: vi.fn(), suggestFollowUps: vi.fn(), updateFollowUp: vi.fn(),
    searchProducts: vi.fn(), summarizeVoiceNote: vi.fn(),
  },
}))

import { api } from '../api/client'
import hcpReducer from '../store/hcpSlice'
import interactionsReducer from '../store/interactionsSlice'
import chatReducer from '../store/chatSlice'
import followUpsReducer from '../store/followUpsSlice'
import StructuredForm from './StructuredForm'
import VoiceNoteRecorder from './VoiceNoteRecorder'

// ---- fake microphone + MediaRecorder -------------------------------------------------
const track = { stop: vi.fn() }
class FakeMediaRecorder {
  static isTypeSupported = (t) => t === 'audio/webm;codecs=opus'
  static last = null
  constructor(stream, opts) {
    this.stream = stream
    this.mimeType = opts?.mimeType || ''
    this.state = 'inactive'
    FakeMediaRecorder.last = this
  }
  start() { this.state = 'recording' }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['fake-audio'], { type: this.mimeType }) })
    this.onstop?.()
  }
}

const makeStore = () =>
  configureStore({
    reducer: { hcps: hcpReducer, interactions: interactionsReducer, chat: chatReducer, followUps: followUpsReducer },
    preloadedState: { hcps: { items: [{ id: 'h1', name: 'Dr. A' }], selectedId: 'h1', status: 'succeeded' } },
  })

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  Object.defineProperty(globalThis.navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [track] })) },
  })
  api.summarizeVoiceNote.mockResolvedValue({
    transcript: 'Met Dr. Rao, discussed CardioMax pricing.',
    summary: 'Discussed CardioMax pricing.',
    consent_at: '2026-11-08T10:00:00Z',
    transcription_model: 'whisper-large-v3-turbo',
  })
  api.searchProducts.mockImplementation(async (q) =>
    [{ id: 'p1', name: 'CardioMax' }, { id: 'p2', name: 'OncoBoost' }].filter((p) => p.name.toLowerCase().includes((q || '').toLowerCase()))
  )
  api.suggestFollowUps.mockResolvedValue([])
  api.listFollowUps.mockResolvedValue([])
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const CONSENT_TEXT = 'HCP consents to this recording being sent to a third-party AI service for transcription. Audio is not stored.'
const openPanel = () => fireEvent.click(screen.getByRole('button', { name: /Summarize from Voice Note \(Requires Consent\)/ }))

describe('VoiceNoteRecorder', () => {
  it('cannot record until consent is given', () => {
    render(<VoiceNoteRecorder onSummary={vi.fn()} />)
    openPanel()
    const record = screen.getByRole('button', { name: /Record/ })
    expect(record.disabled).toBe(true)
    fireEvent.click(record)
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('checkbox', { name: CONSENT_TEXT }))
    expect(screen.getByRole('button', { name: /Record/ }).disabled).toBe(false)
  })

  it('records, uploads once, returns the summary and releases the microphone', async () => {
    const onSummary = vi.fn()
    render(<VoiceNoteRecorder onSummary={onSummary} />)
    openPanel()
    fireEvent.click(screen.getByRole('checkbox'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Record/ })) })
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({ audio: true })
    expect(FakeMediaRecorder.last.mimeType).toBe('audio/webm;codecs=opus')
    expect(screen.getByRole('checkbox').disabled).toBe(true) // consent can't change mid-recording
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Stop/ })) })
    await waitFor(() => expect(onSummary).toHaveBeenCalledWith('Discussed CardioMax pricing.', expect.objectContaining({ consentAt: '2026-11-08T10:00:00Z' })))
    expect(api.summarizeVoiceNote).toHaveBeenCalledTimes(1)
    expect(api.summarizeVoiceNote.mock.calls[0][0].type).toBe('audio/webm;codecs=opus')
    expect(track.stop).toHaveBeenCalled()
    expect(screen.getByText('Summary added to Topics Discussed.')).toBeTruthy()
    expect(screen.getByText('Transcript (not saved)')).toBeTruthy()
  })

  it('shows server errors (e.g. no speech) and a denied microphone', async () => {
    api.summarizeVoiceNote.mockRejectedValueOnce(new Error('API 422: No speech detected in the recording.'))
    render(<VoiceNoteRecorder onSummary={vi.fn()} />)
    openPanel()
    fireEvent.click(screen.getByRole('checkbox'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Record/ })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Stop/ })) })
    expect((await screen.findByRole('alert')).textContent).toContain('No speech detected')

    navigator.mediaDevices.getUserMedia.mockRejectedValueOnce(Object.assign(new Error('nope'), { name: 'NotAllowedError' }))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Record/ })) })
    expect(screen.getByRole('alert').textContent).toBe('Microphone permission was denied.')
  })

  it('explains when the browser cannot record', async () => {
    vi.stubGlobal('MediaRecorder', undefined)
    render(<VoiceNoteRecorder onSummary={vi.fn()} />)
    openPanel()
    fireEvent.click(screen.getByRole('checkbox'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Record/ })) })
    expect(screen.getByRole('alert').textContent).toBe("Recording isn't supported in this browser.")
  })
})

describe('StructuredForm: voice note + products', () => {
  it('puts the summary into Topics Discussed and submits consent + products', async () => {
    api.createInteraction.mockResolvedValue({ id: 'n1', compliance_flag: false })
    render(<Provider store={makeStore()}><StructuredForm /></Provider>)

    fireEvent.change(screen.getByLabelText('Topics Discussed'), { target: { value: 'Typed first.' } })
    openPanel()
    fireEvent.click(screen.getByRole('checkbox'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Record/ })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Stop/ })) })
    await waitFor(() => expect(screen.getByLabelText('Topics Discussed').value).toBe('Typed first.\n\nDiscussed CardioMax pricing.'))
    expect(screen.getByText(/Voice note consent recorded/)).toBeTruthy()

    const products = screen.getByLabelText('Products Discussed')
    fireEvent.change(products, { target: { value: 'cardio' } })
    fireEvent.mouseDown(await screen.findByRole('option', { name: 'CardioMax' }))

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Log Interaction' })) })
    expect(api.createInteraction).toHaveBeenCalledWith(expect.objectContaining({
      raw_notes: 'Typed first.\n\nDiscussed CardioMax pricing.',
      products_discussed: ['CardioMax'],
      voice_note_consent: true,
      voice_note_consent_at: '2026-11-08T10:00:00Z',
    }))
  })

  it('without a voice note, consent is false', async () => {
    api.createInteraction.mockResolvedValue({ id: 'n2', compliance_flag: false })
    render(<Provider store={makeStore()}><StructuredForm /></Provider>)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Log Interaction' })) })
    expect(api.createInteraction).toHaveBeenCalledWith(expect.objectContaining({ voice_note_consent: false, voice_note_consent_at: null, products_discussed: [] }))
  })

  it('sample product suggestions come from the same product catalog', async () => {
    render(<Provider store={makeStore()}><StructuredForm /></Provider>)
    fireEvent.click(screen.getByRole('button', { name: /Add Sample/ }))
    const input = screen.getByLabelText('Sample product')
    await act(async () => { fireEvent.change(input, { target: { value: 'onco' } }) })
    expect(api.searchProducts).toHaveBeenCalledWith('onco')
    const options = [...document.querySelectorAll('#sample-product-options option')].map((o) => o.value)
    expect(options).toEqual(['OncoBoost'])
    expect(input.getAttribute('list')).toBe('sample-product-options')
  })
})

describe('admin-only product catalog in the form', () => {
  it('shows the exact consent wording', () => {
    render(<VoiceNoteRecorder onSummary={vi.fn()} />)
    openPanel()
    expect(screen.getByRole('checkbox', { name: CONSENT_TEXT })).toBeTruthy()
  })

  it('Products Discussed never offers to create a product', async () => {
    render(<Provider store={makeStore()}><StructuredForm /></Provider>)
    fireEvent.change(screen.getByLabelText('Products Discussed'), { target: { value: 'Zylotrex' } })
    await waitFor(() => expect(api.searchProducts).toHaveBeenCalledWith('Zylotrex'))
    expect(document.getElementById('products-options')).toBeNull() // no suggestions, and no "Add" entry
    await act(async () => { fireEvent.keyDown(screen.getByLabelText('Products Discussed'), { key: 'Enter' }) })
    expect(screen.queryByText('Zylotrex')).toBeNull()
  })

  it('a sample must be a catalog product; the catalog spelling is used', async () => {
    render(<Provider store={makeStore()}><StructuredForm /></Provider>)
    fireEvent.click(screen.getByRole('button', { name: /Add Sample/ }))
    fireEvent.change(screen.getByLabelText('Sample product'), { target: { value: 'Mystery Pill' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Add' })) })
    expect(screen.getByRole('alert').textContent).toBe('"Mystery Pill" is not in the product list. Pick a product from the list.')

    fireEvent.change(screen.getByLabelText('Sample product'), { target: { value: 'oncoboost' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Add' })) })
    expect(screen.getByText('OncoBoost × 1')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows the server message for a rejected product', async () => {
    api.createInteraction.mockRejectedValue(new Error('API 422: Unknown product(s): Zylotrex. Pick from the product list.'))
    render(<Provider store={makeStore()}><StructuredForm /></Provider>)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Log Interaction' })) })
    expect(screen.getByText(/Pick from the product list/)).toBeTruthy()
  })
})
