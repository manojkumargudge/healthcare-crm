import { useCallback, useEffect, useRef, useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { submitStructuredInteraction } from '../store/interactionsSlice'
import { selectHcp } from '../store/hcpSlice'
import { api } from '../api/client'
import { SENTIMENTS } from '../lib/format'
import SearchableMultiSelect from './SearchableMultiSelect'
import FollowUpSuggestions from './FollowUpSuggestions'
import VoiceNoteRecorder from './VoiceNoteRecorder'

// Layout and labels follow docs/brief-mockup.png ("Interaction Details").
const INTERACTION_TYPES = ['Meeting', 'Visit', 'Call', 'Email', 'Sample Drop', 'Conference']
// Rep-added materials need compliance approval; using them flags the interaction.
const PENDING = 'Pending approval'
const SENTIMENT_ICONS = { Positive: '🙂', Neutral: '😐', Negative: '🙁' }

const today = () => new Date().toISOString().slice(0, 10)
const nowHHMM = () => new Date().toTimeString().slice(0, 5)

/** "HCP Name" field: search/select an HCP; stays in sync with the sidebar selection. */
function HcpPicker() {
  const dispatch = useDispatch()
  const hcps = useSelector((s) => s.hcps.items)
  const selectedId = useSelector((s) => s.hcps.selectedId)
  const selected = hcps.find((h) => h.id === selectedId)
  const [query, setQuery] = useState(null) // null = show the selected name
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => { if (rootRef.current && !rootRef.current.contains(e.target)) { setOpen(false); setQuery(null) } }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const q = (query ?? '').trim().toLowerCase()
  const matches = hcps.filter((h) => !q || [h.name, h.specialty, h.institution].some((v) => v?.toLowerCase().includes(q)))
  const choose = (h) => { dispatch(selectHcp(h.id)); setQuery(null); setOpen(false) }

  return (
    <div className="multi-select" ref={rootRef}>
      <input
        id="hcp-name"
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls="hcp-name-options"
        autoComplete="off"
        placeholder="Search or select HCP..."
        value={query ?? selected?.name ?? ''}
        onChange={(e) => { setQuery(e.target.value); setOpen(true) }}
        onFocus={() => { setQuery(''); setOpen(true) }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); if (matches[0]) choose(matches[0]) }
          if (e.key === 'Escape') { setOpen(false); setQuery(null) }
        }}
      />
      {open && matches.length > 0 && (
        <ul className="multi-select-options" role="listbox" id="hcp-name-options">
          {matches.map((h) => (
            <li key={h.id} role="option" aria-selected={h.id === selectedId} onMouseDown={(e) => { e.preventDefault(); choose(h) }}>
              {h.name}
              {h.specialty && <span className="option-meta"> · {h.specialty}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function StructuredForm() {
  const dispatch = useDispatch()
  const hcpId = useSelector((s) => s.hcps.selectedId)

  const [type, setType] = useState('Meeting')
  const [date, setDate] = useState(today())
  const [time, setTime] = useState(nowHHMM())
  const [attendees, setAttendees] = useState([])
  const [topics, setTopics] = useState('')
  const [products, setProducts] = useState([])
  const [productOptions, setProductOptions] = useState([])
  const [voiceConsentAt, setVoiceConsentAt] = useState(null)
  const [materials, setMaterials] = useState([])
  const [pendingMaterials, setPendingMaterials] = useState(() => new Set())
  const [showMaterialPicker, setShowMaterialPicker] = useState(false)
  const [samples, setSamples] = useState([]) // {product, qty}
  const [showSampleForm, setShowSampleForm] = useState(false)
  const [sampleDraft, setSampleDraft] = useState({ product: '', qty: 1 })
  const [sampleError, setSampleError] = useState('')
  const [sentiment, setSentiment] = useState('') // optional: '' = not set
  const [outcomes, setOutcomes] = useState('')
  const [followUpActions, setFollowUpActions] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState(null)

  const searchAttendees = useCallback(
    async (q) => (await api.searchHcps(q)).map((h) => h.name).filter(Boolean),
    []
  )
  const toOption = useCallback((m) => {
    if (!m.approved) setPendingMaterials((s) => new Set(s).add(m.name))
    return { value: m.name, badge: m.approved ? undefined : PENDING }
  }, [])
  const searchMaterials = useCallback(async (q) => (await api.searchMaterials(q)).map(toOption), [toOption])
  // One admin-only product catalog for "Products Discussed" and sample products: pick, don't create.
  const searchProducts = useCallback(async (q) => (await api.searchProducts(q)).map((p) => p.name), [])
  const refreshProductOptions = useCallback(async (q) => {
    try { setProductOptions(await searchProducts(q)) } catch { setProductOptions([]) }
  }, [searchProducts])
  const onVoiceSummary = useCallback((summary, { consentAt }) => {
    setTopics((prev) => (prev.trim() ? `${prev.trim()}\n\n${summary}` : summary))
    setVoiceConsentAt(consentAt)
  }, [])
  const createMaterial = useCallback(async (name) => toOption(await api.createMaterial(name)), [toOption])

  const addSample = async () => {
    const typed = sampleDraft.product.trim()
    if (!typed) return
    // Samples must be catalog products (case-insensitive); use the catalog spelling.
    let match
    try {
      match = (await searchProducts(typed)).find((n) => n.toLowerCase() === typed.toLowerCase())
    } catch {
      match = undefined
    }
    if (!match) {
      setSampleError(`"${typed}" is not in the product list. Pick a product from the list.`)
      return
    }
    setSampleError('')
    setSamples([...samples, { product: match, qty: Number(sampleDraft.qty) || 1 }])
    setSampleDraft({ product: '', qty: 1 })
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!hcpId) return
    setSubmitting(true)
    setResult(null)
    try {
      const res = await dispatch(submitStructuredInteraction({
        hcp_id: hcpId,
        interaction_type: type,
        channel: 'form',
        interaction_date: date,
        interaction_time: time || null,
        attendees,
        samples_dropped: samples,
        materials_shared: materials,
        // Rep's own label is authoritative; the backend stores no AI score for it.
        hcp_sentiment: sentiment || null,
        raw_notes: topics,
        products_discussed: products,
        voice_note_consent: Boolean(voiceConsentAt),
        voice_note_consent_at: voiceConsentAt,
        outcomes: outcomes || null,
        follow_up_actions: followUpActions || null,
      })).unwrap()
      setResult(res)
      setAttendees([]); setTopics(''); setProducts([]); setMaterials([]); setSamples([]); setSentiment('')
      setVoiceConsentAt(null)
      setOutcomes(''); setFollowUpActions(''); setShowMaterialPicker(false); setShowSampleForm(false)
      setTime(nowHHMM())
    } catch (err) {
      setResult({ error: err.message })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="panel details-card" onSubmit={handleSubmit}>
      <div className="details-card-header">Interaction Details</div>

      <div className="details-card-body">
        <div className="field-row">
          <div className="field">
            <label className="field-label" htmlFor="hcp-name">HCP Name</label>
            <HcpPicker />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="interaction-type">Interaction Type</label>
            <select id="interaction-type" value={type} onChange={(e) => setType(e.target.value)}>
              {INTERACTION_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
        </div>

        <div className="field-row">
          <div className="field">
            <label className="field-label" htmlFor="interaction-date">Date</label>
            <input id="interaction-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="interaction-time">Time</label>
            <input id="interaction-time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
          </div>
        </div>

        <div className="field">
          <label className="field-label" htmlFor="attendees">Attendees</label>
          <SearchableMultiSelect
            id="attendees"
            values={attendees}
            onChange={setAttendees}
            search={searchAttendees}
            placeholder="Enter names or search..."
          />
        </div>

        <div className="field">
          <label className="field-label" htmlFor="topics">Topics Discussed</label>
          <textarea
            id="topics"
            placeholder="Enter key discussion points..."
            value={topics}
            onChange={(e) => setTopics(e.target.value)}
          />
          <VoiceNoteRecorder onSummary={onVoiceSummary} />
          {voiceConsentAt && (
            <p className="voice-consent-note">Voice note consent recorded {new Date(voiceConsentAt).toLocaleString()}.</p>
          )}
        </div>

        <div className="field">
          <label className="field-label" htmlFor="products">Products Discussed</label>
          <SearchableMultiSelect
            id="products"
            values={products}
            onChange={setProducts}
            search={searchProducts}
            allowCreate={false}
            placeholder="Search products..."
          />
        </div>

        <fieldset className="field group-box">
          <legend className="field-label">Materials Shared / Samples Distributed</legend>

          <div className="sub-card">
            <div className="sub-card-header">
              <label className="sub-card-title" htmlFor="materials">Materials Shared</label>
              <button type="button" className="btn-small" onClick={() => setShowMaterialPicker((v) => !v)}>
                <span aria-hidden="true">🔍</span> Search/Add
              </button>
            </div>
            {showMaterialPicker ? (
              <SearchableMultiSelect
                id="materials"
                values={materials}
                onChange={setMaterials}
                search={searchMaterials}
                onCreate={createMaterial}
                placeholder="Search materials or add a new one"
                createLabel={(q) => `Add "${q}" to materials`}
              />
            ) : materials.length === 0 ? (
              <p className="empty-note">No materials added</p>
            ) : (
              <div className="chip-list">
                {materials.map((m) => (
                  <span className="chip" key={m}>
                    {m}
                    {pendingMaterials.has(m) && <span className="badge-pending">{PENDING}</span>}
                    <button type="button" aria-label={`Remove ${m}`} onClick={() => setMaterials(materials.filter((x) => x !== m))}>×</button>
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="sub-card">
            <div className="sub-card-header">
              <span className="sub-card-title">Samples Distributed</span>
              <button type="button" className="btn-small" onClick={() => setShowSampleForm((v) => !v)}>
                <span aria-hidden="true">⊕</span> Add Sample
              </button>
            </div>
            {showSampleForm && (
              <div className="sample-form">
                <input
                  type="text" placeholder="Product name" aria-label="Sample product"
                  list="sample-product-options"
                  value={sampleDraft.product}
                  onFocus={() => refreshProductOptions(sampleDraft.product)}
                  onChange={(e) => { setSampleDraft({ ...sampleDraft, product: e.target.value }); refreshProductOptions(e.target.value) }}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addSample() } }}
                />
                <datalist id="sample-product-options">
                  {productOptions.map((name) => <option key={name} value={name} />)}
                </datalist>
                <input
                  type="number" min="1" placeholder="Qty" aria-label="Sample quantity"
                  value={sampleDraft.qty}
                  onChange={(e) => setSampleDraft({ ...sampleDraft, qty: e.target.value })}
                />
                <button type="button" className="btn-small" onClick={addSample}>Add</button>
              </div>
            )}
            {sampleError && <p className="form-error sample-error" role="alert">{sampleError}</p>}
            {samples.length === 0 ? (
              !showSampleForm && <p className="empty-note">No samples added</p>
            ) : (
              <div className="chip-list">
                {samples.map((s, idx) => (
                  <span className="chip" key={idx}>
                    {s.product} × {s.qty}
                    <button type="button" aria-label={`Remove ${s.product}`} onClick={() => setSamples(samples.filter((_, i) => i !== idx))}>×</button>
                  </span>
                ))}
              </div>
            )}
          </div>
        </fieldset>

        <fieldset className="field sentiment-field">
          <legend className="field-label">Observed/Inferred HCP Sentiment</legend>
          <div className="sentiment-options">
            {SENTIMENTS.map((s) => (
              <label key={s} className={`sentiment-option ${s.toLowerCase()} ${sentiment === s ? 'checked' : ''}`}>
                <input
                  type="radio"
                  name="hcp_sentiment"
                  value={s}
                  checked={sentiment === s}
                  onChange={() => setSentiment(s)}
                />
                <span className="sentiment-icon" aria-hidden="true">{SENTIMENT_ICONS[s]}</span>
                <span className="sentiment-text">{s}</span>
              </label>
            ))}
            {sentiment && (
              <button type="button" className="btn-link" onClick={() => setSentiment('')}>Clear</button>
            )}
          </div>
        </fieldset>

        <div className="field">
          <label className="field-label" htmlFor="outcomes">Outcomes</label>
          <textarea
            id="outcomes"
            placeholder="Key outcomes or agreements..."
            value={outcomes}
            onChange={(e) => setOutcomes(e.target.value)}
          />
        </div>

        <div className="field">
          <label className="field-label" htmlFor="follow-up-actions">Follow-up Actions</label>
          <textarea
            id="follow-up-actions"
            placeholder="Enter next steps or tasks..."
            value={followUpActions}
            onChange={(e) => setFollowUpActions(e.target.value)}
          />
        </div>

        <FollowUpSuggestions interactionId={result?.id} />

        <button className="btn-primary" type="submit" disabled={!hcpId || submitting}>
          {submitting ? 'Logging…' : 'Log Interaction'}
        </button>

        {result?.error && <p className="form-error">{result.error}</p>}
        {result && !result.error && (
          <p className="form-success">
            Interaction logged{result.compliance_flag ? ` — compliance flag: ${result.compliance_notes}` : '.'}
          </p>
        )}
      </div>
    </form>
  )
}
