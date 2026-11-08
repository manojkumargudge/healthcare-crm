import { useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { login } from '../store/authSlice'
import DemoBanner from './DemoBanner'

export default function LoginPage() {
  const dispatch = useDispatch()
  const { status, error, notice } = useSelector((s) => s.auth)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const submit = (e) => {
    e.preventDefault()
    if (email && password) dispatch(login({ email: email.trim(), password }))
  }

  return (
    <main className="login-page">
      <DemoBanner />
      <form className="login-card" onSubmit={submit} aria-label="Sign in">
        <h1 className="logo">AI-First CRM</h1>
        <p className="login-subtitle">Sign in to log HCP interactions.</p>
        {notice && <p className="login-notice" role="status">{notice}</p>}
        <div className="field">
          <label className="field-label" htmlFor="login-email">Email</label>
          <input id="login-email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="login-password">Password</label>
          <input id="login-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="btn-primary" type="submit" disabled={status === 'loading'}>
          {status === 'loading' ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}
