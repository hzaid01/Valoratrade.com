import Layout from '../components/Layout';
import SystemHealth from '../components/SystemHealth';
import { Shield } from 'lucide-react';

/**
 * System Status Page
 * 
 * Displays:
 * - Capital controller status
 * - Model health
 * - Forward engine metrics
 * - Kill switch status
 */
export default function SystemStatus() {
    return (
        <Layout>
            <div className="max-w-4xl mx-auto">
                <div className="mb-8">
                    <div className="flex items-center space-x-3 mb-2">
                        <Shield className="w-8 h-8 text-primary-500" />
                        <h1 className="text-3xl font-bold text-white">System Status</h1>
                    </div>
                    <p className="text-gray-400">
                        Monitor capital controller, model health, and trading system status.
                    </p>
                </div>

                <SystemHealth />

                {/* Architecture Info */}
                <div className="mt-8 glass-effect rounded-2xl p-6">
                    <h2 className="text-xl font-bold text-white mb-4">System Architecture</h2>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <h3 className="text-sm font-semibold text-primary-500 mb-2">Model Stack</h3>
                            <ul className="space-y-1 text-sm text-gray-400">
                                <li>• <span className="text-white">PatchTST</span> - Temporal embeddings</li>
                                <li>• <span className="text-white">XGBoost</span> - Decision model</li>
                                <li>• Multi-target outputs (prob_up, prob_down, expected_return)</li>
                            </ul>
                        </div>
                        <div>
                            <h3 className="text-sm font-semibold text-primary-500 mb-2">Safety Controls</h3>
                            <ul className="space-y-1 text-sm text-gray-400">
                                <li>• <span className="text-white">Kill Switch</span> - Emergency stop</li>
                                <li>• <span className="text-white">Drawdown Throttle</span> - Auto size reduction</li>
                                <li>• <span className="text-white">Baseline Gates</span> - Must beat naive strategies</li>
                            </ul>
                        </div>
                        <div>
                            <h3 className="text-sm font-semibold text-primary-500 mb-2">Timeframes</h3>
                            <ul className="space-y-1 text-sm text-gray-400">
                                <li>• <span className="text-white">15m</span> - Visualization only</li>
                                <li>• <span className="text-white">1H</span> - <span className="text-primary-400">Decision timeframe (strict)</span></li>
                                <li>• <span className="text-white">4H</span> - Context/visualization</li>
                            </ul>
                        </div>
                        <div>
                            <h3 className="text-sm font-semibold text-primary-500 mb-2">Evaluation</h3>
                            <ul className="space-y-1 text-sm text-gray-400">
                                <li>• <span className="text-white">Forward Engine</span> - Locked predictions</li>
                                <li>• <span className="text-white">Champion/Challenger</span> - Model promotion</li>
                                <li>• <span className="text-white">Walk-Forward</span> - Proper CV</li>
                            </ul>
                        </div>
                    </div>
                </div>
            </div>
        </Layout>
    );
}
