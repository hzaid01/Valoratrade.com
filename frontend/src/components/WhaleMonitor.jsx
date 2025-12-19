import { useEffect, useRef, useState } from 'react';
import { Loader2, TrendingUp, TrendingDown, DollarSign } from 'lucide-react';

const TRANSACTION_THRESHOLD = 50000; // $50,000 threshold for "Whale" trades

export default function WhaleMonitor({ symbol }) {
    const [trades, setTrades] = useState([]);
    const wsRef = useRef(null);

    useEffect(() => {
        setTrades([]); // Clear previous trades on symbol change

        const streamName = `${symbol.toLowerCase()}@aggTrade`;
        const ws = new WebSocket(`wss://stream.binance.com:9443/ws/${streamName}`);
        wsRef.current = ws;

        ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            if (message.e === 'aggTrade') {
                const price = parseFloat(message.p);
                const quantity = parseFloat(message.q);
                const totalValue = price * quantity;

                // Filter for large trades
                if (totalValue >= TRANSACTION_THRESHOLD) {
                    const newTrade = {
                        id: message.a,
                        time: message.E,
                        price: price,
                        quantity: quantity,
                        value: totalValue,
                        isBuyerMaker: message.m, // true means Sell (taker sold into maker buy), false means Buy (taker bought from maker sell)
                        // Wait, per Binance API: 'm': true means the buyer was the maker. So the Taker was a Seller. -> SELL trade.
                        // 'm': false means the seller was the maker. So the Taker was a Buyer. -> BUY trade.
                    };

                    setTrades((prev) => [newTrade, ...prev].slice(0, 50)); // Keep last 50
                }
            }
        };

        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [symbol]);

    return (
        <div className="glass-effect rounded-2xl p-6 h-full flex flex-col">
            <div className="flex items-center space-x-3 mb-4">
                <DollarSign className="w-6 h-6 text-primary-500" />
                <h2 className="text-xl font-bold text-white">Whale Activity (&gt; ${TRANSACTION_THRESHOLD.toLocaleString()})</h2>
            </div>

            <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar">
                {trades.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-48 text-gray-500">
                        <Loader2 className="w-6 h-6 animate-spin mb-2" />
                        <p>Waiting for big trades...</p>
                    </div>
                ) : (
                    <div className="space-y-2">
                        {trades.map((trade) => (
                            <div
                                key={trade.id}
                                className={`p-3 rounded-lg border flex justify-between items-center transition-all animate-in fade-in slide-in-from-right-4 duration-300 ${!trade.isBuyerMaker
                                    ? 'bg-primary-500/10 border-primary-500/30'
                                    : 'bg-danger-500/10 border-danger-500/30'
                                    }`}
                            >
                                <div className="flex items-center space-x-3">
                                    {!trade.isBuyerMaker ? (
                                        <TrendingUp className="w-5 h-5 text-primary-500" />
                                    ) : (
                                        <TrendingDown className="w-5 h-5 text-danger-500" />
                                    )}
                                    <div>
                                        <div className={`font-bold ${!trade.isBuyerMaker ? 'text-primary-500' : 'text-danger-500'}`}>
                                            {!trade.isBuyerMaker ? 'LARGE BUY' : 'LARGE SELL'}
                                        </div>
                                        <div className="text-xs text-gray-400">
                                            {new Date(trade.time).toLocaleTimeString()}
                                        </div>
                                    </div>
                                </div>

                                <div className="text-right">
                                    <div className="text-white font-mono font-bold">
                                        ${trade.value.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                                    </div>
                                    <div className="text-xs text-gray-400">
                                        @ {trade.price.toLocaleString()}
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
