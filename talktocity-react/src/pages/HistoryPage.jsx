import React, { useState, useEffect } from 'react'
import Topbar from '../components/Topbar'
import { getHistory, deleteHistoryEntry } from '../api/history'
import styles from './HistoryPage.module.css'

const CITY_FLAG = { Delhi: '🏛️', Mumbai: '🌊', Udaipur: '🏰' }
const LANG_LABEL = { en: 'EN', hi: 'हि' }

export default function HistoryPage({ onNavigate, user, onLogout, onRerun }) {
  const [entries, setEntries]   = useState([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)
  const [deleting, setDeleting] = useState(null)

  useEffect(() => {
    if (!user) { onNavigate('auth'); return }
    fetchHistory()
  }, [user])

  async function fetchHistory() {
    setLoading(true)
    setError(null)
    try {
      const data = await getHistory()
      setEntries(data.history || [])
    } catch (err) {
      setError('Failed to load history.')
    } finally {
      setLoading(false)
    }
  }

  async function handleDelete(id) {
    setDeleting(id)
    try {
      await deleteHistoryEntry(id)
      setEntries(prev => prev.filter(e => e.id !== id))
    } catch {
      // silently ignore
    } finally {
      setDeleting(null)
    }
  }

  function handleRerun(entry) {
    onRerun({ question: entry.question, city: entry.city, lang: entry.lang })
    onNavigate('search')
  }

  function formatDate(iso) {
    const d = new Date(iso)
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
      + ' · ' + d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
  }

  if (!user) return null

  return (
    <section className={styles.frame}>
      <Topbar activeView="history" onNavigate={onNavigate} user={user} onLogout={onLogout} />

      <div className={styles.shell}>
        <div className={styles.panel}>
          <div className={styles.header}>
            <h1 className={styles.title}>Search History</h1>
            <span className={styles.count}>{entries.length} / 20 searches</span>
          </div>

          {loading && (
            <div className={styles.loadingWrap}>
              {[1,2,3].map(i => <div key={i} className={styles.skeleton} />)}
            </div>
          )}

          {error && <div className={styles.error}>{error}</div>}

          {!loading && entries.length === 0 && (
            <div className={styles.empty}>
              <div className={styles.emptyIcon}>🔍</div>
              <p>No searches yet. Start exploring cities!</p>
              <button className={styles.exploreBtn} onClick={() => onNavigate('search')}>
                Search now
              </button>
            </div>
          )}

          {!loading && entries.length > 0 && (
            <div className={styles.list}>
              {entries.map(entry => (
                <div key={entry.id} className={styles.card}>
                  <div className={styles.cardTop}>
                    <div className={styles.meta}>
                      <span className={styles.city}>
                        {CITY_FLAG[entry.city] || '📍'} {entry.city}
                      </span>
                      <span className={styles.lang}>{LANG_LABEL[entry.lang] || entry.lang}</span>
                      <span className={styles.date}>{formatDate(entry.searched_at)}</span>
                    </div>
                    <button
                      className={styles.deleteBtn}
                      onClick={() => handleDelete(entry.id)}
                      disabled={deleting === entry.id}
                      title="Delete"
                    >
                      {deleting === entry.id ? '…' : '✕'}
                    </button>
                  </div>

                  <p className={styles.question}>{entry.question}</p>

                  {entry.answer && (
                    <p className={styles.answerPreview}>
                      {entry.answer.slice(0, 160)}{entry.answer.length > 160 ? '…' : ''}
                    </p>
                  )}

                  {entry.retrieval_score !== null && (
                    <div className={styles.score}>
                      <span
                        className={styles.scoreDot}
                        style={{ background: entry.retrieval_score >= 0.8 ? '#4ade80' : entry.retrieval_score >= 0.5 ? '#fbbf24' : '#f87171' }}
                      />
                      <span className={styles.scoreText}>
                        {Math.round(entry.retrieval_score * 100)}% match
                      </span>
                    </div>
                  )}

                  <button className={styles.rerunBtn} onClick={() => handleRerun(entry)}>
                    ↻ Search again
                  </button>
                </div>
              ))}
            </div>
          )}

          <a className={styles.backLink} onClick={() => onNavigate('home')}>
            ← Back to city carousel
          </a>
        </div>
      </div>
    </section>
  )
}
