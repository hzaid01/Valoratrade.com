import { useState, useEffect } from 'react';
import { BarChart3, TrendingUp, TrendingDown, Activity } from 'lucide-react';
import { getMarketRegime } from '../lib/api';

/**
 * Market Regime Indicator
 * 
 * Shows current market regime:
 * - TRENDING_UP - Strong uptrend
 * - TRENDING_DOWN - Strong downtrend
 * - RANGING - Sideways/choppy
 * - HIGH_VOLATILITY - Elevated risk
 * - LOW_VOLATILITY - Calm/building
 */
export default function RegimeIndicator({ symbol }) {
    const [regime, setRegime] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (symbol) {
            loadRegime();
            const interval = setInterval(loadRegime, 60000); // Refresh every minute
            return () => clearInterval(interval);
        }
    }, [symbol]); // eslint-disable-line react-hooks/exhaustive-deps

    const loadRegime = async () => {
        try {
            const result = await getMarketRegime(symbol);
            if (result?.success) {
                setRegime(result.data);
            }
        } catch (error) {
            console.error('Failed to load regime:', error);
        } finally {
            setLoading(false);
        }
    };

    const getRegimeConfig = (regimeValue) => {
        const configs = {
            trending_up: {
                label: 'TRENDING UP',
                color: 'text-primary-500',
                bg: 'bg-primary-500/10',
                border: 'border-primary-500',
                icon: TrendingUp,
                description: 'Strong upward momentum'
            },
            trending_down: {
                label: 'TRENDING DOWN',
                color: 'text-danger-500',
                bg: 'bg-danger-500/10',
                border: 'border-danger-500',
                icon: TrendingDown,
                description: 'Strong downward momentum'
            },
            ranging: {
                label: 'RANGING',
                color: 'text-yellow-500',
                bg: 'bg-yellow-500/10',
                border: 'border-yellow-500',
                icon: Activity,
                description: 'Sideways movement, low conviction'
            },
            high_volatility: {
                label: 'HIGH VOLATILITY',
                color: 'text-orange-500',
                bg: 'bg-orange-500/10',
                border: 'border-orange-500',
                icon: BarChart3,
                description: 'Elevated risk, reduced sizing'
            },
            low_volatility: {
                label: 'LOW VOLATILITY',
                color: 'text-blue-500',
                bg: 'bg-blue-500/10',
                border: 'border-blue-500',
                icon: Activity,
                description: 'Calm market, potential breakout'
            },
            unknown: {
                label: 'UNKNOWN',
                color: 'text-gray-500',
                bg: 'bg-gray-500/10',
                border: 'border-gray-500',
                icon: Activity,
                description: 'Insufficient data'
            }
        };

        return configs[regimeValue] || configs.unknown;
    };

    if (loading) {
        return (
            <div className="glass-effect rounded-xl p-4 animate-pulse">
                <div className="h-4 bg-gray-700 rounded w-1/2 mb-2"></div>
                <div className="h-6 bg-gray-700 rounded w-full"></div>
            </div>
        );
    }

    if (!regime) {
        return null;
    }

    const config = getRegimeConfig(regime.regime);
    const Icon = config.icon;

    return (
        <div className={`glass-effect rounded-xl p-4 border ${config.border}`}>
            <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-gray-400 uppercase">Market Regime</span>
                <span className={`text-xs ${regime.is_tradeable ? 'text-primary-500' : 'text-yellow-500'}`}>
                    {regime.is_tradeable ? 'Tradeable' : 'Caution'}
                </span>
            </div>

            <div className="flex items-center space-x-3">
                <div className={`p-2 rounded-lg ${config.bg}`}>
                    <Icon className={`w-5 h-5 ${config.color}`} />
                </div>
                <div>
                    <p className={`font-bold ${config.color}`}>{config.label}</p>
                    <p className="text-xs text-gray-400">{config.description}</p>
                </div>
            </div>

            <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                <div className="text-center">
                    <p className="text-gray-400">ADX</p>
                    <p className="text-white font-medium">{regime.adx?.toFixed(1)}</p>
                </div>
                <div className="text-center">
                    <p className="text-gray-400">Vol Ratio</p>
                    <p className="text-white font-medium">{regime.volatility_ratio?.toFixed(2)}x</p>
                </div>
                <div className="text-center">
                    <p className="text-gray-400">Confidence</p>
                    <p className="text-white font-medium">{(regime.confidence * 100)?.toFixed(0)}%</p>
                </div>
            </div>

            {/* Position size multiplier indicator */}
            <div className="mt-3 pt-3 border-t border-gray-800">
                <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-400">Position Multiplier</span>
                    <span className={`font-medium ${regime.position_multiplier >= 0.8 ? 'text-primary-500' :
                        regime.position_multiplier >= 0.5 ? 'text-yellow-500' :
                            'text-danger-500'
                        }`}>
                        {regime.position_multiplier?.toFixed(1)}x
                    </span>
                </div>
            </div>
        </div>
    );
}
