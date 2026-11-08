import { useEffect, useRef, useState } from 'react'

/**
 * Chips + search box with a suggestion dropdown.
 *
 * props:
 *   values, onChange      selected strings
 *   search(q) -> Promise<string[]>   suggestions for the typed text
 *   allowCreate           Enter on text with no exact match adds it as typed
 *   onCreate(name) -> Promise<string>  optional; returns the canonical name to add
 *   createLabel(q)        text for the "add new" option
 */
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
}) {
  const [query, setQuery] = useState('')
  const [options, setOptions] = useState([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
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
  const visible = options.filter((o) => !selectedLower.includes(o.toLowerCase()))
  const q = query.trim()
  const exact = visible.some((o) => o.toLowerCase() === q.toLowerCase()) || selectedLower.includes(q.toLowerCase())
  const showCreate = allowCreate && q && !exact
  const items = [...visible.map((label) => ({ label, value: label })), ...(showCreate ? [{ label: createLabel(q), value: q, create: true }] : [])]

  useEffect(() => {
    if (!open) return undefined
    const mine = ++requestId.current
    const t = setTimeout(async () => {
      try {
        const results = await search(q)
        if (mine === requestId.current) setOptions(results)
      } catch {
        if (mine === requestId.current) setOptions([])
      }
    }, debounceMs)
    return () => clearTimeout(t)
  }, [q, open, search, debounceMs])

  const add = async (item) => {
    let name = item.value
    if (item.create && onCreate) name = await onCreate(item.value)
    if (name && !values.some((v) => v.toLowerCase() === name.toLowerCase())) onChange([...values, name])
    setQuery('')
    setActive(0)
  }

  const remove = (v) => onChange(values.filter((x) => x !== v))

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
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
