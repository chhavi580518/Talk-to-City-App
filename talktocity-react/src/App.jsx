import React, { useState, useEffect } from 'react'
import HomePage    from './pages/HomePage'
import SearchPage  from './pages/SearchPage'
import AuthPage    from './pages/AuthPage'
import HistoryPage from './pages/HistoryPage'
import { isLoggedIn, getUser, logout } from './api/auth'
import styles from './App.module.css'

export default function App() {
  const [view, setView]       = useState('home')
  const [user, setUser]       = useState(null)
  const [rerunQuery, setRerunQuery] = useState(null)  // { question, city, lang }

  useEffect(() => {
    if (isLoggedIn()) setUser(getUser())
  }, [])

  function navigate(target) {
    setView(target)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function handleLoginSuccess(u) { setUser(u); navigate('home') }
  function handleLogout() { logout(); setUser(null); navigate('home') }

  function handleRerun(query) {
    setRerunQuery(query)
    navigate('search')
  }

  return (
    <div className={styles.page}>
      <div className={styles.ambient} />

      {view === 'home' && (
        <HomePage onNavigate={navigate} user={user} onLogout={handleLogout} />
      )}
      {view === 'search' && (
        <SearchPage
          onNavigate={navigate}
          user={user}
          onLogout={handleLogout}
          rerunQuery={rerunQuery}
          onRerunConsumed={() => setRerunQuery(null)}
        />
      )}
      {view === 'auth' && (
        <AuthPage onNavigate={navigate} onLoginSuccess={handleLoginSuccess} />
      )}
      {view === 'history' && (
        <HistoryPage
          onNavigate={navigate}
          user={user}
          onLogout={handleLogout}
          onRerun={handleRerun}
        />
      )}
    </div>
  )
}
