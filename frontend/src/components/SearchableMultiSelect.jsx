import { useEffect, useRef, useState } from 'react'

/**
 * Chips + search box with a suggestion dropdown.
 *
 * props:
 *   values, onChange      selected strings
 *   search(q) -> Promise<Array<string | {value, badge?}>>   suggestions; `badge` is shown
 *                         next to the option and on its chip (e.g. "Pending approval")
 *   allowCreate           Enter on text with no exact match adds it as typed
 *   onCreate(name) -> Promise<string | {value, badge?}>  optional; returns the canonical entry
 *   createLabel(q)        text for the "add new" option
 *   onBusyChange(busy)    optional; told when an onCreate call starts/ends, so a parent
 *                         form can block submit until the new entry is saved
 */
const normalize = (o) => (typeof o === 'string' ? { value: o } : o)

export default function SearchableMultiSelect({
  id,
  values,
  onChange,
  search,
  placeholder,
  allowCreate = true,
  onCreate,
  createLabel = (q) => `Add "${q}"`,
  debounceMs = 200,
  onBusyChange,
}) {
  const [query, setQuery] = useState('')
  const [options, setOptions] = useState([]) // [{value, badge?}]
  const [badges, setBadges] = useState({}) // value -> badge, remembered for chips
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const requestId = useRef(0)
  const rootRef = useRef(null)

  // Close on any click outside the picker (blur alone is unreliable, e.g. when
  // the window itself isn't focused).
  useEffect(() => {
    if (!open) return undefined
    const onDocMouseDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocMouseDown)
    return () => document.removeEventListener('mousedown', onDocMouseDown)
  }, [open])

  const selectedLower = values.map((v) => v.toLowerCase())
  const q = query.trim()
  // Only options matching the *current* text: results for an earlier query may still be
  // in state until the debounced search returns, and Enter must not pick one of those.
  const visible = options.filter(
    (o) => !selectedLower.includes(o.value.toLowerCase()) && o.value.toLowerCase().includes(q.toLowerCase())
  )
  const exact =
    options.some((o) => o.value.toLowerCase() === q.toLowerCase()) || selectedLower.includes(q.toLowerCase())
  const showCreate = allowCreate && q && !exact
  const items = [
    ...visible.map((o) => ({ label: o.value, value: o.value, badge: o.badge })),
    ...(showCreate ? [{ label: createLabel(q), value: q, create: true }] : []),
  ]

  useEffect(() => {
    if (!open) return undefined
    const mine = ++requestId.current
    const t = setTimeout(async () => {
      try {
        const results = (await search(q)).map(normalize)
        if (mine === requestId.current) {
          setOptions(results)
          remember(results)
        }
      } catch {
        if (mine === requestId.current) setOptions([])
      }
    }, debounceMs)
    return () => clearTimeout(t)
  }, [q, open, search, debounceMs])

  const add = async (item) => {
    if (creating) return
    let entry = { value: item.value, badge: item.badge }
    if (item.create && onCreate) {
      setCreating(true)
      setCreateError('')
      onBusyChange?.(true)
      try {
        entry = normalize(await onCreate(item.value))
      } catch (err) {
        setCreateError(`Couldn’t add "${item.value}": ${err?.message || 'request failed'}`)
        return
      } finally {
        setCreating(false)
        onBusyChange?.(false)
      }
    }
    remember([entry])
    const name = entry.value
    if (name && !values.some((v) => v.toLowerCase() === name.toLowerCase())) onChange([...values, name])
    setQuery('')
    setActive(0)
  }

  const remove = (v) => onChange(values.filter((x) => x !== v))

  function remember(entries) {
    const withBadge = entries.filter((e) => e.badge)
    if (withBadge.length) setBadges((b) => ({ ...b, ...Object.fromEntries(withBadge.map((e) => [e.value, e.badge])) }))
  }

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setActive((a) => Math.min(a + 1, Math.max(items.length - 1, 0)))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(a - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (items[active]) add(items[active])
    } else if (e.key === 'Escape') {
      setOpen(false)
    } else if (e.key === 'Backspace' && !query && values.length) {
      remove(values[values.length - 1])
    }
  }

  const listId = `${id}-options`
  return (
    <div className="multi-select" ref={rootRef}>
      <div className="chip-input">
        {values.map((v) => (
          <div className="chip" key={v}>
            <span>{v}</span>
            {badges[v] && <span className="badge-pending">{badges[v]}</span>}
            <button type="button" aria-label={`Remove ${v}`} onClick={() => remove(v)}>×</button>
          </div>
        ))}
        <input
          id={id}
          type="text"
          role="combobox"
          aria-expanded={open && items.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          value={query}
          readOnly={creating}
          aria-busy={creating}
          placeholder={values.length ? '' : placeholder}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); setActive(0) }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={onKeyDown}
        />
      </div>
      {open && items.length > 0 && (
        <ul className="multi-select-options" role="listbox" id={listId}>
          {items.map((item, i) => (
            <li
              key={`${item.create ? 'new:' : ''}${item.value}`}
              role="option"
              aria-selected={i === active}
              className={`${i === active ? 'active' : ''} ${item.create ? 'create' : ''}`}
              onMouseDown={(e) => { e.preventDefault(); add(item) }}
              onMouseEnter={() => setActive(i)}
            >
              {item.label}
              {item.badge && <span className="badge-pending">{item.badge}</span>}
            </li>
          ))}
        </ul>
      )}
      {creating && <p className="empty-note" role="status">Saving new entry…</p>}
      {createError && <p className="form-error" role="alert">{createError}</p>}
    </div>
  )
}
