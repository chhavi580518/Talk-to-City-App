const TOKEN_KEY = 'talktocity_token'
const USER_KEY  = 'talktocity_user'

export function saveSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}
export function getToken()    { return localStorage.getItem(TOKEN_KEY) }
export function getUser()     { const r = localStorage.getItem(USER_KEY); return r ? JSON.parse(r) : null }
export function clearSession(){ localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY) }
export function isLoggedIn()  { return !!getToken() }

export async function loginWithGoogle(googleIdToken) {
  const res = await fetch('/auth/google', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id_token: googleIdToken }),
  })
  if (!res.ok) throw new Error(`Login failed: ${await res.text()}`)
  const data = await res.json()
  saveSession(data.token, data.user)
  return data.user
}

export function logout() {
  clearSession()
  if (window.google?.accounts?.id) window.google.accounts.id.disableAutoSelect()
}
