import { supabase } from './supabase';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/**
 * Get authentication headers with current session token.
 * Attempts to refresh the session if no valid session exists.
 */
async function getAuthHeaders() {
  // Get current session
  let { data: { session }, error } = await supabase.auth.getSession();
  console.log('[API] getSession result:', { hasSession: !!session, error: error?.message });

  // If no session or session expired, try to refresh
  if (!session || error) {
    console.log('[API] Attempting session refresh...');
    const { data: refreshed, error: refreshError } = await supabase.auth.refreshSession();
    if (refreshed?.session && !refreshError) {
      session = refreshed.session;
      console.log('[API] Session refreshed successfully');
    } else {
      console.log('[API] Session refresh failed:', refreshError?.message);
    }
  }

  if (!session?.access_token) {
    console.warn('[API] No valid session found for API call');
  } else {
    console.log('[API] Token available, length:', session.access_token.length);
  }

  return {
    'Content-Type': 'application/json',
    ...(session?.access_token && { Authorization: `Bearer ${session.access_token}` })
  };
}

/**
 * Parse error response from API.
 */
async function parseErrorResponse(response) {
  try {
    const errorData = await response.json();
    return errorData.detail || `Request failed with status ${response.status}`;
  } catch {
    return `Request failed with status ${response.status}`;
  }
}

/**
 * Fetch wrapper with error handling.
 */
async function apiFetch(url, options = {}) {
  try {
    // Debug: Log request info
    console.log('[API] Fetching:', url);
    console.log('[API] Headers:', {
      ...options.headers,
      Authorization: options.headers?.Authorization ? `Bearer [${options.headers.Authorization.length} chars]` : 'MISSING'
    });

    const response = await fetch(url, options);

    if (!response.ok) {
      console.error('[API] Response not OK:', response.status, response.statusText);
      const errorMessage = await parseErrorResponse(response);
      throw new Error(errorMessage);
    }

    return response.json();
  } catch (error) {
    console.error('[API] Fetch error:', error.message);
    // Network errors (e.g., offline, server not running)
    if (error.name === 'TypeError' && error.message === 'Failed to fetch') {
      throw new Error('Unable to connect to server. Please check your connection.');
    }
    throw error;
  }
}

/**
 * Get top cryptocurrencies by trading volume.
 * @param {number} limit - Number of coins to return (default: 100, max: 500)
 */
export async function getTopCoins(limit = 100) {
  if (limit < 1 || limit > 500) {
    throw new Error('Limit must be between 1 and 500');
  }

  const headers = await getAuthHeaders();
  const result = await apiFetch(`${API_URL}/api/market/top-coins?limit=${limit}`, { headers });
  return result;
}

/**
 * Get detailed analysis for a cryptocurrency symbol.
 * @param {string} symbol - Trading pair symbol (e.g., "BTCUSDT")
 */
export async function analyzeSymbol(symbol) {
  if (!symbol || typeof symbol !== 'string') {
    throw new Error('Invalid symbol');
  }

  const headers = await getAuthHeaders();
  const result = await apiFetch(`${API_URL}/api/market/analyze/${encodeURIComponent(symbol)}`, {
    headers
  });
  return result;
}

/**
 * Get historical K-lines (candlesticks) for a symbol.
 * @param {string} symbol - Trading pair symbol (e.g., "BTCUSDT")
 * @param {string} interval - Kline interval (default: "1h")
 */
export async function getKlines(symbol, interval = '1h') {
  if (!symbol || typeof symbol !== 'string') {
    throw new Error('Invalid symbol');
  }

  const headers = await getAuthHeaders();
  const result = await apiFetch(`${API_URL}/api/market/klines/${encodeURIComponent(symbol)}?interval=${interval}`, {
    headers
  });
  return result;
}

/**
 * Get user settings (API keys are returned masked).
 */
export async function getUserSettings() {
  const headers = await getAuthHeaders();
  const result = await apiFetch(`${API_URL}/api/user/settings`, { headers });
  return result;
}

/**
 * Update user API keys.
 * @param {Object} keys - Object containing API keys to update
 */
export async function updateUserSettings(keys) {
  if (!keys || typeof keys !== 'object') {
    throw new Error('Invalid settings data');
  }

  const headers = await getAuthHeaders();
  const result = await apiFetch(`${API_URL}/api/user/settings`, {
    method: 'POST',
    headers,
    body: JSON.stringify(keys)
  });
  return result;
}
