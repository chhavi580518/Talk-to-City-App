import { getToken } from './auth'

// Re-add the AuthError class export
export class AuthError extends Error {
  constructor(msg) { 
    super(msg); 
    this.name = 'AuthError'; 
  }
}

const TIMEOUT_MS = 120_000

export async function searchCity({ question, city, lang = 'en' }) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json' };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ question, city: city || null, lang }),
      signal: controller.signal,
    });
    clearTimeout(timer);

    // Use the AuthError class here for 401 responses
    if (res.status === 401 && token) {
      throw new AuthError('Session expired. Please sign in again.');
    }
    
    if (!res.ok) {
      throw new Error(`Backend error ${res.status}: ${await res.text()}`);
    }
    
    return res.json();
  } catch (err) {
    clearTimeout(timer);
    if (err.name === 'AbortError') {
      throw new Error('Request timed out. Try a shorter question.');
    }
    throw err;
  }
}