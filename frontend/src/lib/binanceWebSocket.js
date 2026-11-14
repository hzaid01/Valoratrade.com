/**
 * Binance WebSocket utility for real-time kline data.
 *
 * Connects to Binance public WebSocket stream for live candlestick updates.
 * Docs: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
 *
 * Connection state machine (one per subscription):
 *
 *   connecting --open--> open --close/error--> reconnecting --timer--> connecting
 *        |                                         ^
 *        +-----constructor throws / close----------+
 *   any state --cleanup()--> closed   (terminal; no further reconnects)
 *
 * Reconnects use exponential backoff with jitter (1s, 2s, 4s ... capped at
 * 30s), reset after a successful open. Previously: a fixed 3s retry forever,
 * no retry at all if the WebSocket constructor threw, and no way for the UI
 * to know the feed was down.
 */

const BINANCE_WS_URL = 'wss://stream.binance.com:9443/ws';

export const WS_STATUS = Object.freeze({
    CONNECTING: 'connecting',
    OPEN: 'open',
    RECONNECTING: 'reconnecting',
    CLOSED: 'closed',
});

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;

/** Backoff delay for the given attempt (0-based), with +/-20% jitter. */
export function backoffDelay(attempt, random = Math.random) {
    const base = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt);
    const jitter = 1 + (random() * 0.4 - 0.2);
    return Math.round(base * jitter);
}

/**
 * Open a self-reconnecting stream.
 *
 * @param {string} streamName - e.g. 'btcusdt@aggTrade'
 * @param {function} onData - called with each parsed message
 * @param {function} [onStatus] - called with a WS_STATUS value on every change
 * @param {object} [deps] - injectable WebSocket/setTimeout/clearTimeout (tests)
 * @returns {function} cleanup
 */
export function createReconnectingStream(streamName, onData, onStatus, deps = {}) {
    const WS = deps.WebSocket || globalThis.WebSocket;
    const setTimer = deps.setTimeout || globalThis.setTimeout.bind(globalThis);
    const clearTimer = deps.clearTimeout || globalThis.clearTimeout.bind(globalThis);
    const wsUrl = `${BINANCE_WS_URL}/${streamName}`;

    let ws = null;
    let reconnectTimeout = null;
    let attempt = 0;
    let status = null;

    const setStatus = (next) => {
        if (status === next) return;
        status = next;
        if (onStatus) {
            try { onStatus(next); } catch (err) { console.warn('[BinanceWS] onStatus failed:', err); }
        }
    };

    const scheduleReconnect = () => {
        if (status === WS_STATUS.CLOSED || reconnectTimeout) return;
        setStatus(WS_STATUS.RECONNECTING);
        const delay = backoffDelay(attempt);
        attempt += 1;
        console.log(`[BinanceWS] Reconnecting ${streamName} in ${delay}ms (attempt ${attempt})`);
        reconnectTimeout = setTimer(() => {
            reconnectTimeout = null;
            connect();
        }, delay);
    };

    const connect = () => {
        if (status === WS_STATUS.CLOSED) return;
        setStatus(WS_STATUS.CONNECTING);
        let socket;
        try {
            socket = new WS(wsUrl);
        } catch (err) {
            console.error(`[BinanceWS] Failed to create WebSocket for ${streamName}:`, err);
            scheduleReconnect();
            return;
        }
        ws = socket;

        socket.onopen = () => {
            if (ws !== socket) return;
            attempt = 0;
            setStatus(WS_STATUS.OPEN);
            console.log(`[BinanceWS] Connected to ${streamName}`);
        };

        socket.onmessage = (event) => {
            if (ws !== socket) return;
            let data;
            try {
                data = JSON.parse(event.data);
            } catch (err) {
                console.warn(`[BinanceWS] Failed to parse ${streamName} message:`, err);
                return;
            }
            onData(data);
        };

        socket.onerror = (error) => {
            console.error(`[BinanceWS] ${streamName} WebSocket error:`, error);
        };

        socket.onclose = () => {
            if (ws !== socket) return; // stale socket from before a reconnect/cleanup
            ws = null;
            console.log(`[BinanceWS] ${streamName} connection closed`);
            scheduleReconnect();
        };
    };

    connect();

    return () => {
        setStatus(WS_STATUS.CLOSED);
        if (reconnectTimeout) {
            clearTimer(reconnectTimeout);
            reconnectTimeout = null;
        }
        if (ws) {
            const socket = ws;
            ws = null;
            socket.close();
        }
        console.log(`[BinanceWS] Cleaned up ${streamName}`);
    };
}

/**
 * Subscribe to Binance aggregated trade stream for real-time price updates.
 * This provides millisecond-level updates for the last candle (like TradingView).
 *
 * @param {string} symbol - Trading pair (e.g., 'BTCUSDT')
 * @param {function} onTrade - Callback for each trade update
 * @param {function} [onStatus] - Optional connection status callback (WS_STATUS)
 * @returns {function} Cleanup function to close WebSocket
 */
export function subscribeToAggTrades(symbol, onTrade, onStatus) {
    if (!symbol || !onTrade) {
        console.error('[BinanceWS] Missing required parameters for aggTrade');
        return () => { };
    }

    return createReconnectingStream(`${symbol.toLowerCase()}@aggTrade`, (data) => {
        // aggTrade format: { p: price, q: quantity, T: trade time, m: is buyer maker }
        if (data.p && data.T) {
            onTrade({
                price: parseFloat(data.p),
                quantity: parseFloat(data.q),
                time: data.T, // Trade time in milliseconds
                isBuyerMaker: data.m
            });
        }
    }, onStatus);
}

/**
 * Subscribe to Binance kline (candlestick) stream.
 *
 * @param {string} symbol - Trading pair (e.g., 'BTCUSDT')
 * @param {string} interval - Kline interval ('15m', '1h', '4h')
 * @param {function} onKline - Callback for each kline update
 * @param {function} [onStatus] - Optional connection status callback (WS_STATUS)
 * @returns {function} Cleanup function to close WebSocket
 */
export function subscribeToKlines(symbol, interval, onKline, onStatus) {
    if (!symbol || !interval || !onKline) {
        console.error('[BinanceWS] Missing required parameters');
        return () => { };
    }

    return createReconnectingStream(`${symbol.toLowerCase()}@kline_${interval}`, (data) => {
        if (data.k) {
            // Transform Binance kline to lightweight-charts format
            onKline({
                time: Math.floor(data.k.t / 1000), // Convert ms to seconds
                open: parseFloat(data.k.o),
                high: parseFloat(data.k.h),
                low: parseFloat(data.k.l),
                close: parseFloat(data.k.c),
                volume: parseFloat(data.k.v),
                isClosed: data.k.x // Is this kline closed?
            });
        }
    }, onStatus);
}
