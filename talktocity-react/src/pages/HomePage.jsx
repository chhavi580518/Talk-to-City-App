import React, { useEffect } from 'react'
import Topbar from '../components/Topbar'
import { useCarousel, CITIES } from '../hooks/useCarousel'
import styles from './HomePage.module.css'

export default function HomePage({ onNavigate, user, onLogout }) {
  const { current, handleNext, handlePrev, handleDot } = useCarousel()

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'ArrowRight') handleNext()
      if (e.key === 'ArrowLeft')  handlePrev()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handleNext, handlePrev])

  const city = CITIES[current]

  return (
    <section className={styles.frame}>
      {/* Slides (background images) */}
      <div className={styles.slides}>
        {CITIES.map((c, i) => (
          <article
            key={c.name}
            className={`${styles.slide} ${i === current ? styles.active : ''}`}
            data-city={c.name}
          />
        ))}
      </div>

      <Topbar activeView="home" onNavigate={onNavigate} user={user} onLogout={onLogout} />

      <div className={styles.hero}>
        <div className={styles.heroMain}>
          <div className={styles.copy}>
            <div className={styles.eyebrow}>{city.tagline}</div>
            <h1 className={styles.cityTitle}>{city.name.toUpperCase()}</h1>
            <p className={styles.cityDesc}>{city.desc}</p>
          </div>

          <div className={styles.rightRail}>
            <button className={styles.ghostBtn} onClick={() => onNavigate('search')}>
              Explore this city
            </button>
            <div className={styles.quickStat}>
              {String(current + 1).padStart(2, '0')} / {String(CITIES.length).padStart(2, '0')}
            </div>
          </div>
        </div>

        <div className={styles.miniPanels}>
          {CITIES.map(c => (
            <div key={c.name} className={styles.miniCard}>
              <h3>{c.name}</h3>
              <p>{c.desc}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Prev / Next arrows */}
      <div className={styles.controls}>
        <button className={styles.arrow} onClick={handlePrev} aria-label="Previous slide">‹</button>
        <button className={styles.arrow} onClick={handleNext} aria-label="Next slide">›</button>
      </div>

      {/* Dots + copyright */}
      <div className={styles.footerStrip}>
        <div className={styles.dots}>
          {CITIES.map((_, i) => (
            <button
              key={i}
              className={i === current ? styles.dotActive : styles.dot}
              aria-label={`Go to slide ${i + 1}`}
              onClick={() => handleDot(i)}
            />
          ))}
        </div>
        <span>© 2026 TalkToCity</span>
      </div>
    </section>
  )
}
