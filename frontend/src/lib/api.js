import { getIdToken } from './firebase';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/**
 * Get authentication headers with current Firebase ID token.
 */
async function getAuthHeaders() {
  const token = await getIdToken();
  return {
    'Content-Type': 'application/json',
    ...(token && { Authorization: `Bearer ${token}` })
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
    const response = await fetch(url, options);

    if (!response.ok) {
      const errorMessage = await parseErrorResponse(response);
      throw new Error(errorMessage);
    }

    return response.json();
  } catch (error) {
    if (error.name === 'TypeError' && error.message === 'Failed to fetch') {
      throw new Error('Unable to connect to server. Please check your connection.');
    }
    throw error;
  }
}

/**
 * Get top cryptocurrencies by trading volume.
 */
/**
 * Get top cryptocurrencies by trading volume.
 * Uses Backend Proxy to avoid CORS/Geo-blocking.
 */
export async function getTopCoins(limit = 100) {
  try {
    const headers = await getAuthHeaders();
    const result = await apiFetch(`${API_URL}/api/market/top-coins?limit=${limit}`, { headers });

    if (result.success) {
      return result; // Backend returns format { success: true, data: [...] }
    } else {
      throw new Error(result.error || 'Failed to fetch top coins');
    }
  } catch (error) {
    console.warn('[API] Top coins fetch failed:', error);
    return { success: false, error: error.message };
  }
}

/**
 * Get trading signal from production trading system.
 * Uses new PatchTST + XGBoost model stack.
 */
export async function getSignal(symbol) {
  if (!symbol || typeof symbol !== 'string') {
    return { success: false, error: 'Invalid symbol' };
  }

  try {
    const headers = await getAuthHeaders();
    const result = await apiFetch(`${API_URL}/api/signals/${encodeURIComponent(symbol)}`, {
      headers
    });
    return result;
  } catch (error) {
    return { success: false, error: error.message || 'Failed to get signal' };
  }
}

/**
 * Get historical K-lines with multi-timeframe support.
 * Uses Backend Proxy to avoid CORS/Geo-blocking.
 * @param {string} symbol - Trading pair symbol
 * @param {string} interval - '15m', '1h', or '4h'
 */
export async function getKlines(symbol, interval = '1h') {
  if (!symbol || typeof symbol !== 'string') {
    return { success: false, error: 'Invalid symbol' };
  }

  // Validate interval
  if (!['15m', '1h', '4h'].includes(interval)) {
    return { success: false, error: 'Interval must be 15m, 1h, or 4h' };
  }

  try {
    const headers = await getAuthHeaders();
    const url = `${API_URL}/api/market/klines/${encodeURIComponent(symbol)}?interval=${interval}&limit=500`;

    // Fetch from backend
    const result = await apiFetch(url, { headers });

    if (result.success) {
      // Backend already formats data correctly: { time, open, high, low, close, volume }
      // But verify just in case
      return { success: true, data: result.data };
    } else {
      throw new Error(result.error || 'Failed to fetch klines from backend');
    }
  } catch (error) {
    console.error('[API] Kline fetch failed:', error);
    return { success: false, error: error.message || 'Failed to load chart data' };
  }
}

/**
 * Get market regime for a symbol.
 */
export async function getMarketRegime(symbol) {
  const headers = await getAuthHeaders();
  return apiFetch(`${API_URL}/api/market/regime/${encodeURIComponent(symbol)}`, { headers });
}

/**
 * Get capital controller status.
 */
export async function getCapitalStatus() {
  const headers = await getAuthHeaders();
  return apiFetch(`${API_URL}/api/signals/capital/status`, { headers });
}

/**
 * Get capital history for equity curve.
 */
export async function getCapitalHistory(limit = 100) {
  const headers = await getAuthHeaders();
  return apiFetch(`${API_URL}/api/signals/capital/history?limit=${limit}`, { headers });
}

/**
 * Get signal history for a symbol.
 */
export async function getSignalHistory(symbol, limit = 50) {
  if (!symbol) throw new Error('Symbol required');
  const headers = await getAuthHeaders();
  return apiFetch(`${API_URL}/api/signals/history/${encodeURIComponent(symbol)}?limit=${limit}`, { headers });
}

/**
 * Get forward engine metrics.
 */
export async function getForwardMetrics(modelVersion = null, days = 7) {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams({ days });
  if (modelVersion) params.append('model_version', modelVersion);

  return apiFetch(`${API_URL}/api/signals/forward/metrics?${params}`, { headers });
}

/**
 * Run backtest with baselines.
 */
export async function runBacktest(symbol, strategy = 'model') {
  const headers = await getAuthHeaders();
  return apiFetch(`${API_URL}/api/backtest/run`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ symbol, strategy })
  });
}

/**
 * Get all baseline results for a symbol.
 */
export async function getBaselines(symbol) {
  const headers = await getAuthHeaders();
  return apiFetch(`${API_URL}/api/backtest/baselines/${encodeURIComponent(symbol)}`, { headers });
}

/**
 * Get model status and health.
 */
export async function getModelStatus() {
  const headers = await getAuthHeaders();
  return apiFetch(`${API_URL}/api/admin/model-status`, { headers });
}

/**
 * Get champion model for a symbol.
 */
export async function getChampionModel(symbol) {
  const headers = await getAuthHeaders();
  return apiFetch(`${API_URL}/api/admin/champion/${encodeURIComponent(symbol)}`, { headers });
}

/**
 * Legacy: Get analysis for a symbol (backwards compatibility).
 */
export async function analyzeSymbol(symbol) {
  // Use new signal endpoint
  const signalResult = await getSignal(symbol);

  if (!signalResult.success) {
    throw new Error(signalResult.error || 'Failed to get signal');
  }

  const signal = signalResult.data;

  // Transform to old format for backwards compatibility
  return {
    success: true,
    data: {
      symbol: signal.symbol,
      current_price: signal.trade?.entry_price || 0,
      final_signal: signal.signal.toUpperCase(),
      mode: 'live',
      lstm_signal: {
        signal: signal.signal.toUpperCase(),
        confidence: signal.confidence
      },
      ai_decision: {
        reason: `Confidence: ${(signal.confidence * 100).toFixed(1)}% | Regime: ${signal.context?.regime || 'unknown'}`
      },
      support_resistance: {
        support: signal.support_resistance?.support ?? signal.trade?.stop_loss ?? 0,
        resistance: signal.support_resistance?.resistance ?? signal.trade?.take_profit ?? 0
      },
      trade_setup: {
        entry_price: signal.trade?.entry_price || 0,
        stop_loss: signal.trade?.stop_loss || 0,
        take_profit_1: signal.trade?.take_profit || 0,
        take_profit_2: (signal.trade?.take_profit || 0) * 1.01,
        take_profit_3: (signal.trade?.take_profit || 0) * 1.02,
        risk_reward_ratio: '1:2'
      },
      indicators: {
        rsi: signal.indicators?.rsi ?? 50,
        macd: { histogram: signal.indicators?.macd?.histogram ?? 0 },
        ema: {
          ema_9: signal.indicators?.ema?.ema_9 ?? 0,
          ema_21: signal.indicators?.ema?.ema_21 ?? 0,
          ema_50: signal.indicators?.ema?.ema_50 ?? 0
        }
      }
    }
  };
}

/**
 * Get user settings.
 */
export async function getUserSettings() {
  const headers = await getAuthHeaders();
  return apiFetch(`${API_URL}/api/user/settings`, { headers });
}

/**
 * Update user settings.
 */
export async function updateUserSettings(keys) {
  if (!keys || typeof keys !== 'object') {
    throw new Error('Invalid settings data');
  }

  const headers = await getAuthHeaders();
  return apiFetch(`${API_URL}/api/user/settings`, {
    method: 'POST',
    headers,
    body: JSON.stringify(keys)
  });
}
