import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Loader2,
  ArrowLeft,
  Activity,
  Target,
  Shield,
  Brain,
  AlertTriangle,
  TrendingUp,
  TrendingDown
} from 'lucide-react';
import Layout from '../components/Layout';
import CandlestickChart from '../components/CandlestickChart';
import WhaleMonitor from '../components/WhaleMonitor';
import RegimeIndicator from '../components/RegimeIndicator';
import { analyzeSymbol } from '../lib/api';

export default function Analysis() {
  const { symbol } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  // Initialize with skeletal data to prevent "Not Found" state
  const [data, setData] = useState({
    symbol: symbol || 'BTCUSDT',
    current_price: 0,
    final_signal: 'NO_TRADE',
    mode: 'live',
    lstm_signal: { signal: 'HOLD', confidence: 0 },
    ai_decision: { reason: 'Initializing...' },
    support_resistance: { support: 0, resistance: 0 },
    trade_setup: { entry_price: 0, stop_loss: 0, take_profit_1: 0, take_profit_2: 0, take_profit_3: 0, risk_reward_ratio: 'N/A' },
    indicators: { rsi: 50, macd: { histogram: 0 }, ema: { ema_9: 0, ema_21: 0, ema_50: 0 } }
  });
  const [error, setError] = useState('');

  useEffect(() => {
    const loadAnalysis = async () => {
      if (!symbol) return;

      try {
        setLoading(true);
        setError('');
        console.log('Loading analysis for:', symbol);

        const response = await analyzeSymbol(symbol);

        if (response.success) {
          setData(response.data);
        } else {
          console.warn('Analysis failed, using fallback:', response.error);
          // Keep existing skeletal data / update basic fields
          setError(response.error || 'Signal data unavailable');
        }
      } catch (err) {
        console.error('Analysis error:', err);
        setError(err.message || 'Failed to load analysis data');
      } finally {
        setLoading(false);
      }
    };

    if (symbol) {
      loadAnalysis();
    }
  }, [symbol]);

  if (loading) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-primary-500" />
        </div>
      </Layout>
    );
  }

  // With default state, this check is redundant but kept for safety
  if (!data || !data.symbol) {
    return (
      <Layout>
        <div className="max-w-4xl mx-auto">
          <button
            onClick={() => navigate('/dashboard')}
            className="flex items-center space-x-2 text-gray-400 hover:text-white mb-6"
          >
            <ArrowLeft className="w-5 h-5" />
            <span>Back to Dashboard</span>
          </button>
          <div className="bg-danger-500/10 border border-danger-500 text-danger-500 px-6 py-4 rounded-lg">
            CRITICAL DATA FAILURE for {symbol} - Please check backend connection.
          </div>
        </div>
      </Layout>
    );
  }

  const getSignalColor = (signal) => {
    if (signal === 'LONG') return 'text-primary-500';
    if (signal === 'SHORT') return 'text-danger-500';
    return 'text-gray-400';
  };

  const getSignalBg = (signal) => {
    if (signal === 'LONG') return 'bg-primary-500/10 border-primary-500';
    if (signal === 'SHORT') return 'bg-danger-500/10 border-danger-500';
    return 'bg-gray-500/10 border-gray-500';
  };

  return (
    <Layout>
      <div className="max-w-7xl mx-auto">
        <button
          onClick={() => navigate('/dashboard')}
          className="flex items-center space-x-2 text-gray-400 hover:text-white mb-6 transition-colors"
        >
          <ArrowLeft className="w-5 h-5" />
          <span>Back to Dashboard</span>
        </button>

        {error && (
          <div className="bg-amber-500/10 border border-amber-500 text-amber-400 px-6 py-4 rounded-lg mb-6">
            <div className="flex items-center space-x-2">
              <AlertTriangle className="w-5 h-5" />
              <span>{error}</span>
            </div>
          </div>
        )}

        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white mb-2">{data.symbol}</h1>
            <p className="text-gray-400">
              {data.current_price > 0 ? (
                `$${data.current_price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
              ) : (
                'Price loading...'
              )}
            </p>
          </div>
          <div className={`px-4 py-2 rounded-lg text-xs ${data.mode === 'live' ? 'bg-primary-500/10 text-primary-500' : 'bg-gray-500/10 text-gray-400'
            }`}>
            {data.mode === 'live' ? 'Live Mode' : 'Simulated Mode'}
          </div>
        </div>

        {/* Live Chart Section */}
        <CandlestickChart
          symbol={data.symbol}
          supportResistance={data.support_resistance?.support > 0 || data.support_resistance?.resistance > 0 ? data.support_resistance : null}
        />

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">

          {/* Left Column: Regime + Whale Monitor (Takes up 1 column) */}
          <div className="lg:col-span-1 space-y-6">
            {/* Market Regime Indicator */}
            {!error && <RegimeIndicator symbol={data.symbol} />}

            {/* Whale Monitor */}
            {!error && (
              <div className="h-[500px]">
                <WhaleMonitor key={data.symbol} symbol={data.symbol} />
              </div>
            )}
          </div>

          {/* Right Column: AI Signals & Analysis (Takes up 2 columns) */}
          <div className="lg:col-span-2 space-y-6">

            {/* Top Signals Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className={`glass-effect rounded-2xl p-6 border-2 ${getSignalBg(data.final_signal)}`}>
                <div className="flex items-center space-x-3 mb-4">
                  <Brain className="w-6 h-6 text-primary-500" />
                  <h2 className="text-xl font-bold text-white">Final Signal</h2>
                </div>
                <div className={`text-4xl font-bold mb-2 ${getSignalColor(data.final_signal)}`}>
                  {data.final_signal}
                </div>
                <p className="text-sm text-gray-400">{data.ai_decision.reason}</p>
              </div>

              <div className="glass-effect rounded-2xl p-6">
                <div className="flex items-center space-x-3 mb-4">
                  <Activity className="w-6 h-6 text-primary-500" />
                  <h2 className="text-xl font-bold text-white">Model Signal</h2>
                </div>
                <div className={`text-3xl font-bold mb-2 ${getSignalColor(data.lstm_signal.signal)}`}>
                  {data.lstm_signal.signal}
                </div>
                <p className="text-sm text-gray-400">
                  {data.lstm_signal.confidence > 0 ? (
                    `Confidence: ${(data.lstm_signal.confidence * 100).toFixed(1)}%`
                  ) : (
                    'Confidence: N/A'
                  )}
                </p>
              </div>
            </div>

            {/* Support/Resistance & Trade Setup */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="glass-effect rounded-2xl p-6">
                <div className="flex items-center space-x-3 mb-4">
                  <Shield className="w-6 h-6 text-primary-500" />
                  <h2 className="text-xl font-bold text-white">Support & Resistance</h2>
                </div>
                <div className="space-y-2">
                  <div>
                    <p className="text-sm text-gray-400">Resistance</p>
                    <p className="text-lg font-semibold text-danger-500">
                      {data.support_resistance.resistance > 0 ? (
                        `$${data.support_resistance.resistance.toLocaleString()}`
                      ) : (
                        <span className="text-gray-500">N/A</span>
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-400">Support</p>
                    <p className="text-lg font-semibold text-primary-500">
                      {data.support_resistance.support > 0 ? (
                        `$${data.support_resistance.support.toLocaleString()}`
                      ) : (
                        <span className="text-gray-500">N/A</span>
                      )}
                    </p>
                  </div>
                </div>
              </div>

              <div className="glass-effect rounded-2xl p-6">
                <div className="flex items-center space-x-3 mb-4">
                  <Target className="w-6 h-6 text-primary-500" />
                  <h2 className="text-xl font-bold text-white">Trade Setup</h2>
                </div>

                {['LONG', 'SHORT'].includes(data.final_signal) && data.trade_setup.entry_price > 0 ? (
                  <div className="space-y-3">
                    <div className="flex justify-between items-center py-2 border-b border-gray-800">
                      <span className="text-gray-400">Entry Price</span>
                      <span className="text-white font-semibold">
                        ${data.trade_setup.entry_price.toLocaleString()}
                      </span>
                    </div>
                    <div className="flex justify-between items-center py-2 border-b border-gray-800">
                      <span className="text-gray-400">Stop Loss</span>
                      <span className="text-danger-500 font-semibold">
                        ${data.trade_setup.stop_loss.toLocaleString()}
                      </span>
                    </div>
                    <div className="space-y-2">
                      <div className="flex justify-between items-center py-1 text-sm">
                        <span className="text-gray-400">TP 1</span>
                        <span className="text-primary-500 font-semibold">
                          ${data.trade_setup.take_profit_1.toLocaleString()}
                        </span>
                      </div>
                      <div className="flex justify-between items-center py-1 text-sm">
                        <span className="text-gray-400">TP 2</span>
                        <span className="text-primary-500 font-semibold">
                          ${data.trade_setup.take_profit_2.toLocaleString()}
                        </span>
                      </div>
                      <div className="flex justify-between items-center py-1 text-sm">
                        <span className="text-gray-400">TP 3</span>
                        <span className="text-primary-500 font-semibold">
                          ${data.trade_setup.take_profit_3.toLocaleString()}
                        </span>
                      </div>
                    </div>
                    <div className="bg-gray-800/50 rounded-lg p-2 mt-2">
                      <p className="text-xs text-center text-gray-400">Risk/Reward: <span className="text-white font-medium">{data.trade_setup.risk_reward_ratio}</span></p>
                    </div>
                  </div>
                ) : (
                  <div className="text-center py-8 text-gray-400">
                    {error ? 'Trade setup unavailable' : 'No active trade setup.'}
                  </div>
                )}
              </div>
            </div>

            {/* Technical Indicators */}
            <div className="glass-effect rounded-2xl p-6">
              <h2 className="text-xl font-bold text-white mb-4">Technical Indicators</h2>
              <div className="space-y-4">
                <div>
                  <div className="flex justify-between mb-2">
                    <span className="text-gray-400">RSI</span>
                    <span className={`font-semibold ${data.indicators.rsi > 70 ? 'text-danger-500' :
                      data.indicators.rsi < 30 ? 'text-primary-500' :
                        'text-gray-300'
                      }`}>
                      {data.indicators.rsi.toFixed(2)}
                    </span>
                  </div>
                  <div className="w-full bg-gray-800 rounded-full h-2">
                    <div
                      className={`h-2 rounded-full ${data.indicators.rsi > 70 ? 'bg-danger-500' :
                        data.indicators.rsi < 30 ? 'bg-primary-500' :
                          'bg-gray-500'
                        }`}
                      style={{ width: `${Math.min(data.indicators.rsi, 100)}%` }}
                    ></div>
                  </div>
                </div>

                <div className="flex justify-between items-center border-t border-gray-800 pt-3 text-sm">
                  <div>
                    <span className="text-gray-400 block">MACD</span>
                    <span className={`font-semibold ${data.indicators.macd.histogram > 0 ? 'text-primary-500' : 'text-danger-500'}`}>
                      {data.indicators.macd.histogram.toFixed(2)}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-400 block">EMA 9</span>
                    <span className="font-semibold text-gray-300">
                      {data.indicators.ema.ema_9.toFixed(2)}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-400 block">EMA 21</span>
                    <span className="font-semibold text-gray-300">
                      {data.indicators.ema.ema_21.toFixed(2)}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-400 block">EMA 50</span>
                    <span className="font-semibold text-gray-300">
                      {data.indicators.ema.ema_50.toFixed(2)}
                    </span>
                  </div>
                </div>

              </div>
            </div>

          </div>
        </div>

        {data.breaker_blocks && data.breaker_blocks.length > 0 && (
          <div className="glass-effect rounded-2xl p-6">
            <div className="flex items-center space-x-3 mb-4">
              <AlertTriangle className="w-6 h-6 text-primary-500" />
              <h2 className="text-xl font-bold text-white">Breaker Blocks</h2>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {data.breaker_blocks.map((block, index) => (
                <div key={index} className={`p-4 rounded-lg border ${block.type === 'bullish'
                  ? 'bg-primary-500/10 border-primary-500'
                  : 'bg-danger-500/10 border-danger-500'
                  }`}>
                  <div className="flex items-center space-x-2 mb-2">
                    {block.type === 'bullish' ? (
                      <TrendingUp className="w-5 h-5 text-primary-500" />
                    ) : (
                      <TrendingDown className="w-5 h-5 text-danger-500" />
                    )}
                    <span className={`font-semibold capitalize ${block.type === 'bullish' ? 'text-primary-500' : 'text-danger-500'
                      }`}>
                      {block.type}
                    </span>
                  </div>
                  <p className="text-sm text-gray-400">Level: ${block.level.toFixed(2)}</p>
                  <p className="text-xs text-gray-500 mt-1">{new Date(block.timestamp).toLocaleString()}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
}
