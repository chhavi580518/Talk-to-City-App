import { useState, useEffect, useRef, useCallback } from 'react'

export const CITIES = [
  { name: 'Udaipur', tagline: 'City of Lakes', desc: 'Palaces, lakefront sunsets, heritage walks, rooftop dining, and curated local recommendations in Hindi and English.' },
  { name: 'Delhi',   tagline: 'Capital of Layers', desc: 'Monuments, museums, food streets, metro-friendly routes, markets, and trusted city answers backed by curated travel data.' },
  { name: 'Mumbai',  tagline: 'City That Never Pauses', desc: 'Marine Drive, art districts, cafés, nightlife, heritage spots, and smart city discovery for plans, routes, and local exploration.' },
]

const AUTO_INTERVAL = 4500

export function useCarousel() {
  const [current, setCurrent] = useState(0)
  const timerRef = useRef(null)

  const goTo = useCallback((index) => setCurrent((index + CITIES.length) % CITIES.length), [])
  const next  = useCallback(() => goTo(current + 1), [current, goTo])
  const prev  = useCallback(() => goTo(current - 1), [current, goTo])

  const restartAuto = useCallback(() => {
    clearInterval(timerRef.current)
    timerRef.current = setInterval(() => setCurrent(c => (c + 1) % CITIES.length), AUTO_INTERVAL)
  }, [])

  useEffect(() => { restartAuto(); return () => clearInterval(timerRef.current) }, [restartAuto])

  const handleNext = useCallback(() => { next(); restartAuto() }, [next, restartAuto])
  const handlePrev = useCallback(() => { prev(); restartAuto() }, [prev, restartAuto])
  const handleDot  = useCallback((i) => { goTo(i); restartAuto() }, [goTo, restartAuto])

  return { current, handleNext, handlePrev, handleDot, cities: CITIES }
}
