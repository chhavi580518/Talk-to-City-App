/**
 * src/api/recommendations.js
 * Fetch personalised recommendation chips based on search history.
 */
import { getToken } from './auth'

export async function getRecommendations() {
  const token = getToken()
  if (!token) return { recommendations: [] }
  try {
    const res = await fetch('/api/recommendations', {
      headers: { 'Authorization': `Bearer ${token}` },
    })
    if (!res.ok) return { recommendations: [] }
    return res.json()
  } catch {
    return { recommendations: [] }
  }
}
