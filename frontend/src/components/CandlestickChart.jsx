import { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';
import { getKlines } from '../lib/api';

export default function CandlestickChart({ symbol, supportResistance }) {
    const chartContainerRef = useRef();
    const chartRef = useRef();
    const candleSeriesRef = useRef();
    const [loading, setLoading] = useState(true);
    const [currentPrice, setCurrentPrice] = useState(null);

    useEffect(() => {
        if (!chartContainerRef.current) return;

        let chart = null;
        let ws = null;

        const initChart = async () => {
            try {
                // Cleanup existing if any
                if (chartRef.current) {
                    chartRef.current.remove();
                    chartRef.current = null;
                }

                if (!chartContainerRef.current) return;

                chart = createChart(chartContainerRef.current, {
                    layout: {
                        background: { type: ColorType.Solid, color: 'transparent' },
                        textColor: '#9ca3af',
                    },
                    grid: {
                        vertLines: { color: '#374151' },
                        horzLines: { color: '#374151' },
                    },
                    width: chartContainerRef.current.clientWidth,
                    height: 500,
                    timeScale: {
                        timeVisible: true,
                        secondsVisible: false,
                    },
                });

                chartRef.current = chart;

                // Create Candlestick Series - V5 API
                const candleSeries = chart.addSeries(CandlestickSeries, {
                    upColor: '#10b981',
                    downColor: '#ef4444',
                    borderVisible: false,
                    wickUpColor: '#10b981',
                    wickDownColor: '#ef4444',
                });
                candleSeriesRef.current = candleSeries;

                // Fetch Historical Data
                const response = await getKlines(symbol, '1h');
                if (response.success && response.data && response.data.length > 0) {
                    const uniqueData = [...new Map(response.data.map(item => [item.time, item])).values()];
                    uniqueData.sort((a, b) => a.time - b.time);

                    candleSeries.setData(uniqueData);
                    if (uniqueData.length > 0) {
                        setCurrentPrice(uniqueData[uniqueData.length - 1]);
                    }
                }

                setLoading(false);

                // WebSocket for Live Updates
                const streamName = `${symbol.toLowerCase()}@kline_1h`;
                ws = new WebSocket(`wss://stream.binance.com:9443/ws/${streamName}`);

                ws.onmessage = (event) => {
                    try {
                        const message = JSON.parse(event.data);
                        if (message.k && candleSeriesRef.current) {
                            const candle = message.k;
                            const newCandle = {
                                time: candle.t / 1000,
                                open: parseFloat(candle.o),
                                high: parseFloat(candle.h),
                                low: parseFloat(candle.l),
                                close: parseFloat(candle.c),
                            };
                            candleSeriesRef.current.update(newCandle);
                            setCurrentPrice(newCandle);
                        }
                    } catch (msgError) {
                        console.warn("Error processing WS message:", msgError);
                    }
                };

                ws.onerror = (e) => {
                    console.warn("WebSocket error:", e);
                };

                // Resize handler
                const handleResize = () => {
                    if (chart && chartContainerRef.current) {
                        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
                    }
                };
                window.addEventListener('resize', handleResize);

            } catch (err) {
                console.error("Critical Chart Error:", err);
                setLoading(false);
            }
        };

        initChart();

        return () => {
            if (ws) ws.close();
            if (chartRef.current) {
                chartRef.current.remove();
                chartRef.current = null;
                candleSeriesRef.current = null;
            }
        };
    }, [symbol]);

    // Update Support/Resistance Lines
    useEffect(() => {
        if (!candleSeriesRef.current || !supportResistance || loading) return;

        // Support Line (Green)
        candleSeriesRef.current.createPriceLine({
            price: supportResistance.support,
            color: '#10b981',
            lineWidth: 2,
            lineStyle: 1, // Dotted
            axisLabelVisible: true,
            title: 'SUPPORT',
        });

        // Resistance Line (Red)
        candleSeriesRef.current.createPriceLine({
            price: supportResistance.resistance,
            color: '#ef4444',
            lineWidth: 2,
            lineStyle: 1, // Dotted
            axisLabelVisible: true,
            title: 'RESISTANCE',
        });

    }, [loading, supportResistance]);

    return (
        <div className="glass-effect rounded-2xl p-6 mb-6">
            <div className="flex justify-between items-center mb-4">
                <h2 className="text-xl font-bold text-white">Live Chart ({symbol})</h2>
                {currentPrice && (
                    <div className="flex space-x-4 text-sm font-mono">
                        <span className="text-gray-400">O: <span className="text-white">{currentPrice.open.toFixed(2)}</span></span>
                        <span className="text-gray-400">H: <span className="text-white">{currentPrice.high.toFixed(2)}</span></span>
                        <span className="text-gray-400">L: <span className="text-white">{currentPrice.low.toFixed(2)}</span></span>
                        <span className="text-gray-400">C: <span className={currentPrice.close >= currentPrice.open ? "text-primary-500" : "text-danger-500"}>{currentPrice.close.toFixed(2)}</span></span>
                    </div>
                )}
            </div>
            <div
                ref={chartContainerRef}
                className="w-full h-[500px] rounded-lg overflow-hidden"
            />
            <div className="mt-2 flex space-x-4 text-xs text-gray-500 justify-center">
                <div className="flex items-center"><div className="w-3 h-0.5 bg-primary-500 mr-1"></div> Support</div>
                <div className="flex items-center"><div className="w-3 h-0.5 bg-danger-500 mr-1"></div> Resistance</div>
            </div>
        </div>
    );
}
