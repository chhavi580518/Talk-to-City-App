/**
 * src/api/history.js
 * Fetch and manage search history for the logged-in user.
 */
import { getToken } from './auth'

async function authFetch(url, options = {}) {
  const token = getToken()
  if (!token) throw new Error('Not logged in')
  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
      ...(options.headers || {}),
    },
  })
  if (!res.ok) throw new Error(`Request failed: ${res.status}`)
  return res.json()
}

export function getHistory() {
  return authFetch('/api/history')
}

export function deleteHistoryEntry(id) {
  return authFetch(`/api/history/${id}`, { method: 'DELETE' })
}
