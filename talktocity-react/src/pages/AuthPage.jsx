import React, { useEffect, useRef, useState } from 'react'
import Topbar from '../components/Topbar'
import { loginWithGoogle } from '../api/auth'
import styles from './AuthPage.module.css'

const GOOGLE_CLIENT_ID = '336469588868-bv6adr8ub9bp15ccco0a44gch9oojdva.apps.googleusercontent.com'

export default function AuthPage({ onNavigate, onLoginSuccess }) {
  const googleBtnRef = useRef(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  useEffect(() => {
    const init = () => {
      if (!window.google?.accounts?.id) return
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: handleCredentialResponse,
        auto_select: false,
      })
      window.google.accounts.id.renderButton(googleBtnRef.current, {
        theme: 'outline', size: 'large', width: 340, text: 'signin_with', shape: 'pill',
      })
    }
    if (window.google?.accounts?.id) { init() }
    else { window.addEventListener('load', init); return () => window.removeEventListener('load', init) }
  }, [])

  async function handleCredentialResponse(response) {
    setLoading(true); setError(null)
    try {
      const user = await loginWithGoogle(response.credential)
      onLoginSuccess(user)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className={styles.frame}>
      <Topbar activeView="auth" onNavigate={onNavigate} user={null} />
      <div className={styles.shell}>
        <div className={styles.card}>
          <div className={styles.logo}>
            <div className={styles.logoMark} />
            <span>TalkToCity</span>
          </div>
          <h1 className={styles.title}>Welcome</h1>
          <p className={styles.subtitle}>Sign in with your Google account to search for travel information about Delhi, Mumbai, and Udaipur.</p>
          <div className={styles.divider} />
          <div className={styles.googleBtnWrap}>
            {loading ? <div className={styles.signingIn}>Signing in...</div> : <div ref={googleBtnRef} />}
          </div>
          {error && <div className={styles.error}>{error}</div>}
          <p className={styles.terms}>By signing in you agree to our terms of service. We only use your name and profile picture from Google.</p>
          <a className={styles.backLink} onClick={() => onNavigate('home')}>← Back to city carousel</a>
        </div>
      </div>
    </section>
  )
}
