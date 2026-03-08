import { useState, useEffect, useRef } from 'react';
import { createChart, CrosshairMode, ColorType, CandlestickSeries, HistogramSeries } from 'lightweight-charts';
import { getKlines, getSignalHistory } from '../lib/api';
import { subscribeToKlines, subscribeToAggTrades } from '../lib/binanceWebSocket';

/**
 * Multi-timeframe candlestick chart
 * 
 * Supports 15m, 1H, 4H timeframes
 * Note: Model decisions use 1H only (strict decision timeframe)
 */
export default function CandlestickChart({ symbol, supportResistance }) {
    const chartContainerRef = useRef(null);
    const chartRef = useRef(null);
    const candlestickSeriesRef = useRef(null);
    const volumeSeriesRef = useRef(null);
    const wsCleanupRef = useRef(null);
    const aggTradeCleanupRef = useRef(null);
    const lastCandleRef = useRef(null); // Track the current candle for aggTrade updates

    const [interval, setInterval] = useState('1h');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    const intervals = [
        { value: '15m', label: '15M', description: 'Visualization' },
        { value: '1h', label: '1H', description: 'Decision TF', isDecision: true },
        { value: '4h', label: '4H', description: 'Context' }
    ];

    useEffect(() => {
        if (!chartContainerRef.current) return;

        // Create chart
        const chart = createChart(chartContainerRef.current, {
            layout: {
                background: { type: ColorType.Solid, color: 'transparent' },
                textColor: '#9ca3af'
            },
            grid: {
                vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
                horzLines: { color: 'rgba(255, 255, 255, 0.05)' }
            },
            crosshair: {
                mode: CrosshairMode.Normal
            },
            rightPriceScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)'
            },
            timeScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)',
                timeVisible: true
            }
        });

        chartRef.current = chart;

        // Handle resize
        const handleResize = () => {
            if (chartContainerRef.current) {
                chart.applyOptions({
                    width: chartContainerRef.current.clientWidth
                });
            }
        };

        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            chart.remove();
            chartRef.current = null;
            candlestickSeriesRef.current = null;
            volumeSeriesRef.current = null;
            lastCandleRef.current = null;
        };
    }, []);

    useEffect(() => {
        if (chartRef.current && symbol) {
            loadData();
        }
    }, [symbol, interval]); // eslint-disable-line react-hooks/exhaustive-deps

    // WebSocket subscription for live updates
    useEffect(() => {
        if (!symbol || !candlestickSeriesRef.current || loading) return;

        // Cleanup previous subscriptions
        if (wsCleanupRef.current) {
            wsCleanupRef.current();
        }
        if (aggTradeCleanupRef.current) {
            aggTradeCleanupRef.current();
        }

        // Subscribe to live kline stream
        wsCleanupRef.current = subscribeToKlines(symbol, interval, (kline) => {
            if (!candlestickSeriesRef.current || !volumeSeriesRef.current) return;

            // Store the current candle data for aggTrade updates
            lastCandleRef.current = {
                time: kline.time,
                open: kline.open,
                high: kline.high,
                low: kline.low,
                close: kline.close,
                volume: kline.volume
            };

            // Update the candlestick
            candlestickSeriesRef.current.update({
                time: kline.time,
                open: kline.open,
                high: kline.high,
                low: kline.low,
                close: kline.close
            });

            // Update the volume bar
            volumeSeriesRef.current.update({
                time: kline.time,
                value: kline.volume,
                color: kline.close >= kline.open ? 'rgba(0, 206, 201, 0.3)' : 'rgba(255, 107, 107, 0.3)'
            });
        });

        // Subscribe to aggTrade stream for millisecond-level updates on the last candle
        aggTradeCleanupRef.current = subscribeToAggTrades(symbol, (trade) => {
            if (!candlestickSeriesRef.current || !lastCandleRef.current) return;

            const candle = lastCandleRef.current;
            const newPrice = trade.price;

            // Update the candle with the new trade price
            const updatedCandle = {
                time: candle.time,
                open: candle.open,
                high: Math.max(candle.high, newPrice),
                low: Math.min(candle.low, newPrice),
                close: newPrice
            };

            // Update the ref
            lastCandleRef.current = {
                ...candle,
                high: updatedCandle.high,
                low: updatedCandle.low,
                close: updatedCandle.close
            };

            // Update the chart
            candlestickSeriesRef.current.update(updatedCandle);
        });

        return () => {
            if (wsCleanupRef.current) {
                wsCleanupRef.current();
                wsCleanupRef.current = null;
            }
            if (aggTradeCleanupRef.current) {
                aggTradeCleanupRef.current();
                aggTradeCleanupRef.current = null;
            }
        };
    }, [symbol, interval, loading]);

    const loadData = async () => {
        try {
            setLoading(true);
            setError(null);

            const result = await getKlines(symbol, interval);

            if (!result.success) {
                throw new Error(result.error || 'Failed to load chart data');
            }

            const chart = chartRef.current;
            if (!chart) return;

            // Remove existing series explicitly
            if (candlestickSeriesRef.current) {
                try {
                    chart.removeSeries(candlestickSeriesRef.current);
                } catch (e) {
                    console.warn("Failed to remove candle series", e);
                }
                candlestickSeriesRef.current = null;
            }
            if (volumeSeriesRef.current) {
                try {
                    chart.removeSeries(volumeSeriesRef.current);
                } catch (e) {
                    console.warn("Failed to remove volume series", e);
                }
                volumeSeriesRef.current = null;
            }

            // Add candlestick series (v5 style)
            const candlestickSeries = chart.addSeries(CandlestickSeries, {
                upColor: '#00cec9',
                downColor: '#ff6b6b',
                borderDownColor: '#ff6b6b',
                borderUpColor: '#00cec9',
                wickDownColor: '#ff6b6b',
                wickUpColor: '#00cec9'
            });
            candlestickSeriesRef.current = candlestickSeries;

            candlestickSeries.setData(result.data);

            // Add volume series (v5 style)
            const volumeSeries = chart.addSeries(HistogramSeries, {
                color: '#26a69a',
                priceFormat: {
                    type: 'volume'
                },
                priceScaleId: '',
                scaleMargins: {
                    top: 0.8,
                    bottom: 0
                }
            });
            volumeSeriesRef.current = volumeSeries;

            const volumeData = result.data.map(d => ({
                time: d.time,
                value: d.volume,
                color: d.close >= d.open ? 'rgba(0, 206, 201, 0.3)' : 'rgba(255, 107, 107, 0.3)'
            }));

            volumeSeries.setData(volumeData);

            // Add support/resistance lines if available
            if (supportResistance) {
                // Resistance line
                if (supportResistance.resistance) {
                    const resistanceLine = {
                        price: supportResistance.resistance,
                        color: '#ff6b6b',
                        lineWidth: 1,
                        lineStyle: 2, // Dashed
                        axisLabelVisible: true,
                        title: 'R'
                    };
                    candlestickSeries.createPriceLine(resistanceLine);
                }

                // Support line
                if (supportResistance.support) {
                    const supportLine = {
                        price: supportResistance.support,
                        color: '#00cec9',
                        lineWidth: 1,
                        lineStyle: 2,
                        axisLabelVisible: true,
                        title: 'S'
                    };
                    candlestickSeries.createPriceLine(supportLine);
                }
            }

            // Add markers for signal history
            try {
                const historyResult = await getSignalHistory(symbol);
                if (historyResult.success) {
                    const markers = historyResult.data
                        .filter(s => s.entry_price && s.direction !== 'hold')
                        .map(s => ({
                            time: new Date(s.timestamp).getTime() / 1000,
                            position: s.direction === 'long' ? 'belowBar' : 'aboveBar',
                            color: s.direction === 'long' ? '#00cec9' : '#ff6b6b',
                            shape: s.direction === 'long' ? 'arrowUp' : 'arrowDown',
                            text: s.model_version.includes('v') ? s.model_version.split('v')[1] : 'AI',
                            size: 1
                        }))
                        .sort((a, b) => a.time - b.time);

                    candlestickSeries.setMarkers(markers);
                }
            } catch (err) {
                console.warn('Failed to load markers', err);
            }

            chart.timeScale().fitContent();

        } catch (err) {
            console.error('Chart data loading error:', err);
            // Provide more user-friendly error messages
            if (err.message && err.message.includes('Invalid symbol')) {
                setError(`Invalid trading pair: ${symbol}. Please check the symbol format.`);
            } else if (err.message && err.message.includes('404')) {
                setError(`Trading pair ${symbol} not found. It may not be available on Binance.`);
            } else {
                setError(err.message || 'Failed to load chart data');
            }
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="glass-effect rounded-2xl p-4 mb-6">
            {/* Timeframe Selector */}
            <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold text-white">Price Chart</h3>
                <div className="flex space-x-2">
                    {intervals.map((tf) => (
                        <button
                            key={tf.value}
                            onClick={() => setInterval(tf.value)}
                            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${interval === tf.value
                                ? tf.isDecision
                                    ? 'bg-primary-500 text-white ring-2 ring-primary-400'
                                    : 'bg-gray-700 text-white'
                                : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                                }`}
                            title={tf.description}
                        >
                            {tf.label}
                            {tf.isDecision && (
                                <span className="ml-1 text-xs opacity-75">★</span>
                            )}
                        </button>
                    ))}
                </div>
            </div>

            {/* Decision Timeframe Notice */}
            {interval === '1h' && (
                <div className="mb-3 px-3 py-1.5 bg-primary-500/10 border border-primary-500/30 rounded-lg">
                    <p className="text-xs text-primary-400">
                        <span className="font-medium">Decision Timeframe:</span> All model signals are based on 1H candles only.
                    </p>
                </div>
            )}

            {/* Chart Container */}
            <div
                ref={chartContainerRef}
                className="w-full h-[400px] relative"
            >
                {loading && (
                    <div className="absolute inset-0 flex items-center justify-center bg-gray-900/50">
                        <div className="animate-spin w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full"></div>
                    </div>
                )}
                {error && !loading && (
                    <div className="absolute inset-0 flex items-center justify-center bg-gray-900/80 rounded-lg">
                        <div className="text-center px-4">
                            <p className="text-danger-500 font-medium mb-2">{error}</p>
                            <p className="text-gray-400 text-sm">The chart will appear once data is available.</p>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
