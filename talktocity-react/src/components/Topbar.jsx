import React from 'react'
import styles from './Topbar.module.css'

export default function Topbar({ activeView, onNavigate, user, onLogout }) {
  return (
    <header className={styles.topbar}>
      <div className={styles.brand} onClick={() => onNavigate('home')}>
        <div className={styles.brandMark} />
        <span>TalkToCity</span>
      </div>

      <nav className={styles.navLinks}>
        <a className={activeView === 'home'    ? styles.active : ''} onClick={() => onNavigate('home')}>
          Popular Locations
        </a>
        <a className={activeView === 'search'  ? styles.active : ''} onClick={() => onNavigate('search')}>
          Search City
        </a>
        {user && (
          <a className={activeView === 'history' ? styles.active : ''} onClick={() => onNavigate('history')}>
            History
          </a>
        )}
        {!user && (
          <a className={activeView === 'auth' ? styles.active : ''} onClick={() => onNavigate('auth')}>
            Sign In
          </a>
        )}
      </nav>

      <div className={styles.right}>
        {user ? (
          <div className={styles.userMenu}>
            {user.picture && (
              <img src={user.picture} alt={user.name} className={styles.avatar} referrerPolicy="no-referrer" />
            )}
            <span className={styles.userName}>{user.name.split(' ')[0]}</span>
            <button className={styles.logoutBtn} onClick={onLogout}>Sign out</button>
          </div>
        ) : (
          <div className={styles.socials}>
            <span>f</span><span>◎</span><span>𝕏</span>
          </div>
        )}
      </div>
    </header>
  )
}
