import { useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import Sidebar from './components/Sidebar'
import LogInteractionScreen from './components/LogInteractionScreen'
import LoginPage from './components/LoginPage'
import AdminScreen from './components/AdminScreen'
import { logout } from './store/authSlice'

export default function App() {
  const dispatch = useDispatch()
  const { token, user } = useSelector((s) => s.auth)
  const [screen, setScreen] = useState('log')

  if (!token || !user) return <LoginPage />

  const isAdmin = user.role === 'admin'
  const current = isAdmin ? screen : 'log' // role-based: only admins can reach admin screens

  return (
    <div className="app-frame">
      <header className="top-bar">
        <nav className="top-nav" aria-label="Main">
          <button type="button" className={current === 'log' ? 'active' : ''} onClick={() => setScreen('log')}>Interactions</button>
          {isAdmin && (
            <button type="button" className={current === 'admin' ? 'active' : ''} onClick={() => setScreen('admin')}>Admin</button>
          )}
        </nav>
        <div className="top-user">
          <span>{user.email} <span className="role-badge">{user.role}</span></span>
          <button type="button" className="btn-link" onClick={() => dispatch(logout())}>Sign out</button>
        </div>
      </header>
      <div className="app-shell">
        <Sidebar />
        {current === 'admin' ? <AdminScreen currentUserId={user.id} /> : <LogInteractionScreen />}
      </div>
    </div>
  )
}
