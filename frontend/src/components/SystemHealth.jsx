import { useState, useEffect } from 'react';
import {
    Activity,
    AlertTriangle,
    TrendingUp,
    TrendingDown,
    Shield,
    Zap
} from 'lucide-react';
import { getCapitalStatus, getForwardMetrics, getModelStatus } from '../lib/api';
import EquityChart from './EquityChart';

/**
 * System Health Panel
 * 
 * Displays:
 * - Capital controller status
 * - Model health
 * - Forward engine metrics
 * - Kill switch status
 */
export default function SystemHealth() {
    const [capitalStatus, setCapitalStatus] = useState(null);
    const [modelStatus, setModelStatus] = useState(null);
    const [forwardMetrics, setForwardMetrics] = useState(null);
    const [loading, setLoading] = useState(true);


    useEffect(() => {
        loadData();
        const interval = setInterval(loadData, 30000); // Refresh every 30s
        return () => clearInterval(interval);
    }, []);

    const loadData = async () => {
        try {
            const [capital, model, forward] = await Promise.all([
                getCapitalStatus().catch(() => null),
                getModelStatus().catch(() => null),
                getForwardMetrics().catch(() => null)
            ]);

            if (capital?.success) setCapitalStatus(capital.data);
            if (model?.success) setModelStatus(model.data);
            if (forward?.success) setForwardMetrics(forward.data);
        } catch {
            // Silently fail on connection error
        } finally {
            setLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="glass-effect rounded-2xl p-6 animate-pulse">
                <div className="h-6 bg-gray-700 rounded w-1/3 mb-4"></div>
                <div className="space-y-3">
                    <div className="h-4 bg-gray-700 rounded w-full"></div>
                    <div className="h-4 bg-gray-700 rounded w-2/3"></div>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* Capital Controller */}
            <div className="glass-effect rounded-2xl p-6">
                <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center space-x-3">
                        <Shield className="w-6 h-6 text-primary-500" />
                        <h2 className="text-xl font-bold text-white">Capital Controller</h2>
                    </div>
                    {capitalStatus?.is_killed && (
                        <span className="px-3 py-1 bg-danger-500/20 text-danger-500 rounded-full text-sm font-medium animate-pulse">
                            KILL SWITCH ACTIVE
                        </span>
                    )}
                </div>

                {capitalStatus ? (
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div className="bg-gray-800/50 rounded-lg p-3">
                            <p className="text-xs text-gray-400 mb-1">Equity</p>
                            <p className="text-lg font-semibold text-white">
                                ${capitalStatus.equity?.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                            </p>
                        </div>
                        <div className="bg-gray-800/50 rounded-lg p-3">
                            <p className="text-xs text-gray-400 mb-1">Drawdown</p>
                            <p className={`text-lg font-semibold ${capitalStatus.drawdown_pct > 0.1 ? 'text-danger-500' :
                                capitalStatus.drawdown_pct > 0.05 ? 'text-yellow-500' :
                                    'text-primary-500'
                                }`}>
                                {(capitalStatus.drawdown_pct * 100).toFixed(1)}%
                            </p>
                        </div>
                        <div className="bg-gray-800/50 rounded-lg p-3">
                            <p className="text-xs text-gray-400 mb-1">Exposure</p>
                            <p className="text-lg font-semibold text-white">
                                {(capitalStatus.exposure_pct * 100).toFixed(1)}%
                            </p>
                        </div>
                        <div className="bg-gray-800/50 rounded-lg p-3">
                            <p className="text-xs text-gray-400 mb-1">Positions</p>
                            <p className="text-lg font-semibold text-white">
                                {capitalStatus.positions_count || 0}
                            </p>
                        </div>
                    </div>
                ) : (
                    <p className="text-gray-400">Unable to load capital status</p>
                )}

                {/* Equity Curve */}
                <div className="mt-4 pt-4 border-t border-gray-800">
                    <p className="text-sm font-medium text-gray-300 mb-2">Account Growth Estimate</p>
                    <EquityChart />
                </div>
            </div>

            {/* Model Health */}
            <div className="glass-effect rounded-2xl p-6">
                <div className="flex items-center space-x-3 mb-4">
                    <Activity className="w-6 h-6 text-primary-500" />
                    <h2 className="text-xl font-bold text-white">Model Health</h2>
                </div>

                {modelStatus ? (
                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <span className="text-gray-400">Active Champions</span>
                            <span className="text-white font-semibold">{modelStatus.champions || 0}</span>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-gray-400">Total Models</span>
                            <span className="text-white font-semibold">{modelStatus.total_models || 0}</span>
                        </div>

                        {modelStatus.champion_details?.length > 0 && (
                            <div className="border-t border-gray-800 pt-4 mt-4">
                                <p className="text-sm text-gray-400 mb-2">Champion Models</p>
                                <div className="space-y-2">
                                    {modelStatus.champion_details.map((model, i) => (
                                        <div key={i} className="flex items-center justify-between bg-gray-800/50 rounded-lg p-2">
                                            <span className="text-white text-sm">{model.symbol}</span>
                                            <span className="text-xs text-gray-400">v{model.version}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                ) : (
                    <p className="text-gray-400">Unable to load model status</p>
                )}
            </div>

            {/* Forward Metrics */}
            <div className="glass-effect rounded-2xl p-6">
                <div className="flex items-center space-x-3 mb-4">
                    <Zap className="w-6 h-6 text-primary-500" />
                    <h2 className="text-xl font-bold text-white">Forward Performance</h2>
                </div>

                {forwardMetrics ? (
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div className="bg-gray-800/50 rounded-lg p-3">
                            <p className="text-xs text-gray-400 mb-1">Accuracy</p>
                            <p className={`text-lg font-semibold ${forwardMetrics.accuracy > 0.55 ? 'text-primary-500' :
                                forwardMetrics.accuracy > 0.5 ? 'text-yellow-500' :
                                    'text-danger-500'
                                }`}>
                                {(forwardMetrics.accuracy * 100).toFixed(1)}%
                            </p>
                        </div>
                        <div className="bg-gray-800/50 rounded-lg p-3">
                            <p className="text-xs text-gray-400 mb-1">Profitable</p>
                            <p className={`text-lg font-semibold ${forwardMetrics.profitable_rate > 0.5 ? 'text-primary-500' : 'text-danger-500'
                                }`}>
                                {(forwardMetrics.profitable_rate * 100).toFixed(1)}%
                            </p>
                        </div>
                        <div className="bg-gray-800/50 rounded-lg p-3">
                            <p className="text-xs text-gray-400 mb-1">Predictions</p>
                            <p className="text-lg font-semibold text-white">
                                {forwardMetrics.resolved || 0}
                            </p>
                        </div>
                        <div className="bg-gray-800/50 rounded-lg p-3">
                            <p className="text-xs text-gray-400 mb-1">Pending</p>
                            <p className="text-lg font-semibold text-white">
                                {forwardMetrics.pending || 0}
                            </p>
                        </div>
                    </div>
                ) : (
                    <p className="text-gray-400">Unable to load forward metrics</p>
                )}
            </div>
        </div >
    );
}
