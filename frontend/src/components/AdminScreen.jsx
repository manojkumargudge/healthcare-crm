import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'

const ROLES = ['rep', 'manager', 'admin']

function useLoader(fn) {
  const [items, setItems] = useState([])
  const [error, setError] = useState('')
  const reload = useCallback(async () => {
    try { setItems(await fn()); setError('') } catch (e) { setError(e.message) }
  }, [fn])
  useEffect(() => { reload() }, [reload])
  return { items, error, setError, reload }
}

function PendingMaterials() {
  const { items, error, setError, reload } = useLoader(api.pendingMaterials)
  const [message, setMessage] = useState('')
  const approve = async (m) => {
    try {
      const r = await api.approveMaterial(m.id)
      setMessage(`Approved "${m.name}": ${r.interactions_rechecked} interaction(s) re-checked, ${r.flags_cleared} compliance flag(s) cleared.`)
      await reload()
    } catch (e) { setError(e.message) }
  }
  return (
    <section className="panel admin-section" aria-labelledby="admin-materials">
      <h2 id="admin-materials">Materials pending approval</h2>
      {message && <p className="form-success" role="status">{message}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {items.length === 0 ? <p className="empty-note">Nothing pending.</p> : (
        <ul className="admin-list">
          {items.map((m) => (
            <li key={m.id}>
              <span>{m.name}</span>
              <button type="button" className="btn-small" onClick={() => approve(m)}>Approve</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Products() {
  const { items, error, setError, reload } = useLoader(api.listProducts)
  const [name, setName] = useState('')
  const [editing, setEditing] = useState(null) // {id, name}
  const run = async (fn) => { try { await fn(); setError(''); await reload() } catch (e) { setError(e.message) } }
  return (
    <section className="panel admin-section" aria-labelledby="admin-products">
      <h2 id="admin-products">Products</h2>
      {error && <p className="form-error" role="alert">{error}</p>}
      <form className="admin-inline" onSubmit={(e) => { e.preventDefault(); if (name.trim()) run(async () => { await api.addProduct(name.trim()); setName('') }) }}>
        <input aria-label="New product name" placeholder="New product name" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn-small" type="submit">Add product</button>
      </form>
      <ul className="admin-list">
        {items.map((p) => (
          <li key={p.id}>
            {editing?.id === p.id ? (
              <form className="admin-inline" onSubmit={(e) => { e.preventDefault(); run(async () => { await api.renameProduct(p.id, editing.name); setEditing(null) }) }}>
                <input aria-label={`Rename ${p.name}`} value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
                <button className="btn-small" type="submit">Save</button>
                <button className="btn-link" type="button" onClick={() => setEditing(null)}>Cancel</button>
              </form>
            ) : (
              <>
                <span>{p.name}</span>
                <span className="admin-actions">
                  <button type="button" className="btn-link" onClick={() => setEditing({ id: p.id, name: p.name })}>Rename</button>
                  <button type="button" className="btn-link danger" onClick={() => { if (window.confirm(`Remove "${p.name}" from the catalog?`)) run(() => api.deleteProduct(p.id)) }}>Remove</button>
                </span>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Users({ currentUserId, demoMode }) {
  const { items, error, setError, reload } = useLoader(api.listUsers)
  const [draft, setDraft] = useState({ email: '', password: '', role: 'rep' })
  const run = async (fn) => { try { await fn(); setError(''); await reload() } catch (e) { setError(e.message) } }
  return (
    <section className="panel admin-section" aria-labelledby="admin-users">
      <h2 id="admin-users">Reps</h2>
      <p className="empty-note">Every app user is a rep. Role decides access: rep = own interactions only, manager = reads everyone's, admin = manages the app.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      {demoMode && (
        <p className="demo-readonly" data-testid="users-read-only">
          Demo mode: user management is read-only, so the shared demo accounts can’t be changed.
        </p>
      )}
      {!demoMode && (
      <form className="admin-inline" onSubmit={(e) => { e.preventDefault(); run(async () => { await api.createUser(draft); setDraft({ email: '', password: '', role: 'rep' }) }) }}>
        <input aria-label="Email" type="email" placeholder="email" value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} required />
        <input aria-label="Initial password" type="password" placeholder="password (12+ chars)" minLength={12} value={draft.password} onChange={(e) => setDraft({ ...draft, password: e.target.value })} required />
        <select aria-label="Role" value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value })}>
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button className="btn-small" type="submit">Add rep</button>
      </form>
      )}
      <table className="admin-table">
        <thead><tr><th>Email</th><th>Role</th><th>Status</th><th /></tr></thead>
        <tbody>
          {items.map((u) => (
            <tr key={u.id}>
              <td>{u.email}{u.id === currentUserId && <span className="option-meta"> (you)</span>}</td>
              <td>
                {demoMode ? u.role : (
                <select aria-label={`Role for ${u.email}`} value={u.role} onChange={(e) => run(() => api.updateUser(u.id, { role: e.target.value }))}>
                  {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                )}
              </td>
              <td>{u.is_active ? 'Active' : 'Inactive'}</td>
              <td className="admin-actions">
                {!demoMode && (<>
                <button type="button" className="btn-link" onClick={() => run(() => api.updateUser(u.id, { is_active: !u.is_active }))}>
                  {u.is_active ? 'Deactivate' : 'Activate'}
                </button>
                <button type="button" className="btn-link" onClick={() => {
                  const pw = window.prompt(`New password for ${u.email} (12+ characters)`)
                  if (pw) run(() => api.updateUser(u.id, { password: pw }))
                }}>Reset password</button>
                </>)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

export default function AdminScreen({ currentUserId, demoMode = false }) {
  return (
    <main className="main admin-screen">
      <div className="page-header"><h1 className="page-title">Admin</h1></div>
      <PendingMaterials />
      <Products />
      <Users currentUserId={currentUserId} demoMode={demoMode} />
    </main>
  )
}
