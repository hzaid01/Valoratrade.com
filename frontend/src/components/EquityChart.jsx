import { useState, useEffect } from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { Loader2 } from 'lucide-react';
import { getCapitalHistory } from '../lib/api';

export default function EquityChart() {
    const [data, setData] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadHistory();
        const interval = setInterval(loadHistory, 60000); // Refresh every minute
        return () => clearInterval(interval);
    }, []);

    const loadHistory = async () => {
        try {
            const result = await getCapitalHistory(50); // Last 50 points
            if (result.success) {
                // Format for chart
                const formatted = result.data.map(item => ({
                    time: new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
                    equity: item.equity,
                    drawdown: (item.drawdown_pct * 100).toFixed(1)
                }));
                setData(formatted);
            }
        } catch (error) {
            console.error('Failed to load equity history', error);
        } finally {
            setLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center h-48">
                <Loader2 className="w-6 h-6 animate-spin text-primary-500" />
            </div>
        );
    }

    if (data.length === 0) {
        return (
            <div className="flex items-center justify-center h-48 text-gray-400 text-sm">
                No equity history available
            </div>
        );
    }

    return (
        <div className="h-48 w-full mt-4">
            <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data}>
                    <defs>
                        <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#00cec9" stopOpacity={0.3} />
                            <stop offset="95%" stopColor="#00cec9" stopOpacity={0} />
                        </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis
                        dataKey="time"
                        tick={{ fill: '#9ca3af', fontSize: 10 }}
                        tickLine={false}
                        axisLine={false}
                    />
                    <YAxis
                        tick={{ fill: '#9ca3af', fontSize: 10 }}
                        tickLine={false}
                        axisLine={false}
                        domain={['auto', 'auto']}
                        tickFormatter={(value) => `$${value / 1000}k`}
                    />
                    <Tooltip
                        contentStyle={{ backgroundColor: '#1f2937', border: 'none', borderRadius: '0.5rem' }}
                        itemStyle={{ color: '#fff' }}
                        labelStyle={{ color: '#9ca3af' }}
                    />
                    <Area
                        type="monotone"
                        dataKey="equity"
                        stroke="#00cec9"
                        fillOpacity={1}
                        fill="url(#colorEquity)"
                    />
                </AreaChart>
            </ResponsiveContainer>
        </div>
    );
}
