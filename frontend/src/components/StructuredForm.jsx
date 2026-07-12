import { useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { submitStructuredInteraction } from '../store/interactionsSlice'
import ChipInput from './ChipInput'

const INTERACTION_TYPES = ['Visit', 'Call', 'Email', 'Sample Drop', 'Conference']

export default function StructuredForm() {
  const dispatch = useDispatch()
  const hcpId = useSelector((s) => s.hcps.selectedId)

  const [type, setType] = useState('Visit')
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [products, setProducts] = useState([])
  const [topics, setTopics] = useState([])
  const [materials, setMaterials] = useState([])
  const [samples, setSamples] = useState([]) // {product, qty}
  const [sampleDraft, setSampleDraft] = useState({ product: '', qty: 1 })
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState(null)

  const addSample = () => {
    if (!sampleDraft.product.trim()) return
    setSamples([...samples, { ...sampleDraft, qty: Number(sampleDraft.qty) || 1 }])
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
        products_discussed: products,
        samples_dropped: samples,
        materials_shared: materials,
        key_topics: topics,
        raw_notes: notes,
      })).unwrap()
      setResult(res)
      setProducts([]); setTopics([]); setMaterials([]); setSamples([]); setNotes('')
    } catch (err) {
      setResult({ error: err.message })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="panel" onSubmit={handleSubmit}>
      <div className="field-row">
        <div className="field">
          <label className="field-label" htmlFor="interaction-type">Interaction type</label>
          <select id="interaction-type" value={type} onChange={(e) => setType(e.target.value)}>
            {INTERACTION_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="interaction-date">Date</label>
          <input id="interaction-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </div>
      </div>

      <div className="field">
        <label className="field-label">Products discussed</label>
        <ChipInput values={products} onChange={setProducts} placeholder="Type a product name, press Enter" />
      </div>

      <div className="field">
        <label className="field-label">Key topics</label>
        <ChipInput values={topics} onChange={setTopics} placeholder="e.g. efficacy data, pricing, side effects" />
      </div>

      <div className="field">
        <label className="field-label">Materials shared</label>
        <ChipInput values={materials} onChange={setMaterials} placeholder="e.g. clinical study leave-behind" />
      </div>

      <div className="field">
        <label className="field-label">Samples dropped</label>
        <div className="field-row" style={{ marginBottom: 8 }}>
          <input
            type="text" placeholder="Product name"
            value={sampleDraft.product}
            onChange={(e) => setSampleDraft({ ...sampleDraft, product: e.target.value })}
          />
          <input
            type="number" min="1" placeholder="Qty"
            value={sampleDraft.qty}
            onChange={(e) => setSampleDraft({ ...sampleDraft, qty: e.target.value })}
          />
        </div>
        <button type="button" className="btn-secondary" onClick={addSample}>Add sample</button>
        {samples.length > 0 && (
          <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {samples.map((s, idx) => (
              <span className="chip" key={idx}>
                {s.product} × {s.qty}
                <button type="button" onClick={() => setSamples(samples.filter((_, i) => i !== idx))}>×</button>
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="field">
        <label className="field-label" htmlFor="notes">Notes</label>
        <textarea
          id="notes"
          placeholder="Any additional context about the interaction…"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </div>

      <button className="btn-primary" type="submit" disabled={!hcpId || submitting}>
        {submitting ? 'Logging…' : 'Log interaction'}
      </button>

      {result?.error && <p style={{ color: 'var(--red-700)', marginTop: 12 }}>{result.error}</p>}
      {result && !result.error && (
        <p style={{ color: 'var(--teal-700)', marginTop: 12, fontSize: 13, fontWeight: 600 }}>
          Interaction logged{result.compliance_flag ? ' — compliance flag raised, see history below.' : '.'}
        </p>
      )}
    </form>
  )
}
