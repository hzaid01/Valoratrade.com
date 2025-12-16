import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Loader2,
  ArrowLeft,
  TrendingUp,
  TrendingDown,
  Activity,
  Target,
  Shield,
  Brain,
  AlertTriangle
} from 'lucide-react';
import Layout from '../components/Layout';
import { analyzeSymbol } from '../lib/api';

export default function Analysis() {
  const { symbol } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const loadAnalysis = async () => {
      try {
        setLoading(true);
        setError('');
        const response = await analyzeSymbol(symbol);
        if (response.success) {
          setData(response.data);
        }
      } catch (err) {
        setError(err.message || 'Failed to load analysis');
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

  if (error) {
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
            {error}
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

        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white mb-2">{data.symbol}</h1>
            <p className="text-gray-400">
              ${data.current_price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </p>
          </div>
          <div className={`px-4 py-2 rounded-lg text-xs ${data.mode === 'live' ? 'bg-primary-500/10 text-primary-500' : 'bg-gray-500/10 text-gray-400'
            }`}>
            {data.mode === 'live' ? 'Live Mode' : 'Simulated Mode'}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
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
              <h2 className="text-xl font-bold text-white">LSTM Signal</h2>
            </div>
            <div className={`text-3xl font-bold mb-2 ${getSignalColor(data.lstm_signal.signal)}`}>
              {data.lstm_signal.signal}
            </div>
            <p className="text-sm text-gray-400">
              Confidence: {(data.lstm_signal.confidence * 100).toFixed(1)}%
            </p>
          </div>

          <div className="glass-effect rounded-2xl p-6">
            <div className="flex items-center space-x-3 mb-4">
              <Shield className="w-6 h-6 text-primary-500" />
              <h2 className="text-xl font-bold text-white">Support & Resistance</h2>
            </div>
            <div className="space-y-2">
              <div>
                <p className="text-sm text-gray-400">Resistance</p>
                <p className="text-lg font-semibold text-danger-500">
                  ${data.support_resistance.resistance.toLocaleString()}
                </p>
              </div>
              <div>
                <p className="text-sm text-gray-400">Support</p>
                <p className="text-lg font-semibold text-primary-500">
                  ${data.support_resistance.support.toLocaleString()}
                </p>
              </div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
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

              <div>
                <div className="flex justify-between mb-2">
                  <span className="text-gray-400">MACD</span>
                  <span className={`font-semibold ${data.indicators.macd.histogram > 0 ? 'text-primary-500' : 'text-danger-500'
                    }`}>
                    {data.indicators.macd.macd.toFixed(2)}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <p className="text-gray-500">Signal</p>
                    <p className="text-gray-300">{data.indicators.macd.signal.toFixed(2)}</p>
                  </div>
                  <div>
                    <p className="text-gray-500">Histogram</p>
                    <p className={data.indicators.macd.histogram > 0 ? 'text-primary-500' : 'text-danger-500'}>
                      {data.indicators.macd.histogram.toFixed(2)}
                    </p>
                  </div>
                </div>
              </div>

              <div>
                <p className="text-gray-400 mb-2">EMAs</p>
                <div className="grid grid-cols-3 gap-2 text-sm">
                  <div>
                    <p className="text-gray-500">EMA 9</p>
                    <p className="text-gray-300">{data.indicators.ema.ema_9.toFixed(2)}</p>
                  </div>
                  <div>
                    <p className="text-gray-500">EMA 21</p>
                    <p className="text-gray-300">{data.indicators.ema.ema_21.toFixed(2)}</p>
                  </div>
                  <div>
                    <p className="text-gray-500">EMA 50</p>
                    <p className="text-gray-300">{data.indicators.ema.ema_50.toFixed(2)}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="glass-effect rounded-2xl p-6">
            <div className="flex items-center space-x-3 mb-4">
              <Target className="w-6 h-6 text-primary-500" />
              <h2 className="text-xl font-bold text-white">Trade Setup</h2>
            </div>

            {data.final_signal !== 'HOLD' ? (
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
                  <div className="flex justify-between items-center py-2">
                    <span className="text-gray-400">Take Profit 1</span>
                    <span className="text-primary-500 font-semibold">
                      ${data.trade_setup.take_profit_1.toLocaleString()}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2">
                    <span className="text-gray-400">Take Profit 2</span>
                    <span className="text-primary-500 font-semibold">
                      ${data.trade_setup.take_profit_2.toLocaleString()}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-2">
                    <span className="text-gray-400">Take Profit 3</span>
                    <span className="text-primary-500 font-semibold">
                      ${data.trade_setup.take_profit_3.toLocaleString()}
                    </span>
                  </div>
                </div>
                <div className="bg-gray-800/50 rounded-lg p-3 mt-4">
                  <p className="text-sm text-gray-400">Risk/Reward Ratio</p>
                  <p className="text-white font-medium">{data.trade_setup.risk_reward_ratio}</p>
                </div>
              </div>
            ) : (
              <div className="text-center py-8 text-gray-400">
                No active trade setup. Signal is HOLD.
              </div>
            )}
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
