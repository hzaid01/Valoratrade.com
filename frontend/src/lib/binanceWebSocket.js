/**
 * Binance WebSocket utility for real-time kline data.
 * 
 * Connects to Binance public WebSocket stream for live candlestick updates.
 * Docs: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
 */

const BINANCE_WS_URL = 'wss://stream.binance.com:9443/ws';

/**
 * Subscribe to Binance aggregated trade stream for real-time price updates.
 * This provides millisecond-level updates for the last candle (like TradingView).
 * 
 * @param {string} symbol - Trading pair (e.g., 'BTCUSDT')
 * @param {function} onTrade - Callback for each trade update
 * @returns {function} Cleanup function to close WebSocket
 */
export function subscribeToAggTrades(symbol, onTrade) {
    if (!symbol || !onTrade) {
        console.error('[BinanceWS] Missing required parameters for aggTrade');
        return () => { };
    }

    const streamName = `${symbol.toLowerCase()}@aggTrade`;
    const wsUrl = `${BINANCE_WS_URL}/${streamName}`;

    let ws = null;
    let reconnectTimeout = null;
    let isCleanedUp = false;

    const connect = () => {
        if (isCleanedUp) return;

        try {
            ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                console.log(`[BinanceWS] Connected to ${streamName}`);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);

                    // aggTrade format: { p: price, q: quantity, T: trade time, m: is buyer maker }
                    if (data.p && data.T) {
                        const trade = {
                            price: parseFloat(data.p),
                            quantity: parseFloat(data.q),
                            time: data.T, // Trade time in milliseconds
                            isBuyerMaker: data.m
                        };

                        onTrade(trade);
                    }
                } catch (err) {
                    console.warn('[BinanceWS] Failed to parse aggTrade message:', err);
                }
            };

            ws.onerror = (error) => {
                console.error('[BinanceWS] aggTrade WebSocket error:', error);
            };

            ws.onclose = () => {
                console.log('[BinanceWS] aggTrade connection closed');

                // Attempt reconnect after 3 seconds if not cleaned up
                if (!isCleanedUp) {
                    reconnectTimeout = setTimeout(() => {
                        console.log('[BinanceWS] Reconnecting aggTrade...');
                        connect();
                    }, 3000);
                }
            };
        } catch (err) {
            console.error('[BinanceWS] Failed to create aggTrade WebSocket:', err);
        }
    };

    // Start connection
    connect();

    // Return cleanup function
    return () => {
        isCleanedUp = true;

        if (reconnectTimeout) {
            clearTimeout(reconnectTimeout);
        }

        if (ws) {
            ws.close();
            ws = null;
        }

        console.log(`[BinanceWS] Cleaned up ${streamName}`);
    };
}

/**
 * Subscribe to Binance kline (candlestick) stream.
 * 
 * @param {string} symbol - Trading pair (e.g., 'BTCUSDT')
 * @param {string} interval - Kline interval ('15m', '1h', '4h')
 * @param {function} onKline - Callback for each kline update
 * @returns {function} Cleanup function to close WebSocket
 */
export function subscribeToKlines(symbol, interval, onKline) {
    if (!symbol || !interval || !onKline) {
        console.error('[BinanceWS] Missing required parameters');
        return () => { };
    }

    const streamName = `${symbol.toLowerCase()}@kline_${interval}`;
    const wsUrl = `${BINANCE_WS_URL}/${streamName}`;

    let ws = null;
    let reconnectTimeout = null;
    let isCleanedUp = false;

    const connect = () => {
        if (isCleanedUp) return;

        try {
            ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                console.log(`[BinanceWS] Connected to ${streamName}`);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);

                    if (data.k) {
                        // Transform Binance kline to lightweight-charts format
                        const kline = {
                            time: Math.floor(data.k.t / 1000), // Convert ms to seconds
                            open: parseFloat(data.k.o),
                            high: parseFloat(data.k.h),
                            low: parseFloat(data.k.l),
                            close: parseFloat(data.k.c),
                            volume: parseFloat(data.k.v),
                            isClosed: data.k.x // Is this kline closed?
                        };

                        onKline(kline);
                    }
                } catch (err) {
                    console.warn('[BinanceWS] Failed to parse message:', err);
                }
            };

            ws.onerror = (error) => {
                console.error('[BinanceWS] WebSocket error:', error);
            };

            ws.onclose = () => {
                console.log('[BinanceWS] Connection closed');

                // Attempt reconnect after 3 seconds if not cleaned up
                if (!isCleanedUp) {
                    reconnectTimeout = setTimeout(() => {
                        console.log('[BinanceWS] Reconnecting...');
                        connect();
                    }, 3000);
                }
            };
        } catch (err) {
            console.error('[BinanceWS] Failed to create WebSocket:', err);
        }
    };

    // Start connection
    connect();

    // Return cleanup function
    return () => {
        isCleanedUp = true;

        if (reconnectTimeout) {
            clearTimeout(reconnectTimeout);
        }

        if (ws) {
            ws.close();
            ws = null;
        }

        console.log(`[BinanceWS] Cleaned up ${streamName}`);
    };
}
