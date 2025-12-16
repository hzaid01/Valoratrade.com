import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Save, Key, AlertCircle, CheckCircle2, XCircle, RefreshCw } from 'lucide-react';
import Layout from '../components/Layout';
import { getUserSettings, updateUserSettings } from '../lib/api';
import { useAuthStore } from '../store/authStore';
import { supabase } from '../lib/supabase';

export default function Settings() {
  const navigate = useNavigate();
  const { user, session } = useAuthStore();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState('');
  const [authError, setAuthError] = useState(false);
  const [keyStatus, setKeyStatus] = useState({
    has_binance_keys: false,
    has_openai_key: false
  });
  const [formData, setFormData] = useState({
    binance_api_key: '',
    binance_secret_key: '',
    openai_api_key: '',
  });


  useEffect(() => {
    const loadSettings = async () => {
      try {
        // Wait for session to be available - don't load if no session
        if (!session?.access_token) {
          console.warn('No session available when loading settings, waiting...');
          // Give a brief moment for session to initialize, then retry
          await new Promise(resolve => setTimeout(resolve, 500));

          // Check session again after delay
          const { data: { session: refreshedSession } } = await supabase.auth.getSession();
          if (!refreshedSession?.access_token) {
            setAuthError(true);
            setError('Not authenticated. Please log in to access settings.');
            setLoading(false);
            return;
          }
        }

        const response = await getUserSettings();
        if (response.success) {
          // Store key status and masked values
          setKeyStatus({
            has_binance_keys: response.data.has_binance_keys || false,
            has_openai_key: response.data.has_openai_key || false
          });
          // Set empty form data - show placeholders for existing keys
          setFormData({
            binance_api_key: '',
            binance_secret_key: '',
            openai_api_key: '',
          });
          // Clear any previous auth error
          setAuthError(false);
          setError('');
        }
      } catch (err) {
        const errorMsg = err.message || 'Failed to load settings';
        // Check if it's an authentication error
        if (errorMsg.includes('401') || errorMsg.includes('Unauthorized') ||
          errorMsg.includes('Authentication') || errorMsg.includes('token')) {
          setAuthError(true);
          setError('Authentication failed. Please try logging in again.');
        } else {
          setError(errorMsg);
        }
      } finally {
        setLoading(false);
      }
    };

    loadSettings();
  }, [session]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setSuccess(false);
    setSaving(true);

    try {
      // Only send non-empty values
      const dataToSend = {};
      if (formData.binance_api_key.trim()) {
        dataToSend.binance_api_key = formData.binance_api_key;
      }
      if (formData.binance_secret_key.trim()) {
        dataToSend.binance_secret_key = formData.binance_secret_key;
      }
      if (formData.openai_api_key.trim()) {
        dataToSend.openai_api_key = formData.openai_api_key;
      }

      await updateUserSettings(dataToSend);
      setSuccess(true);

      // Update key status
      if (dataToSend.binance_api_key && dataToSend.binance_secret_key) {
        setKeyStatus(prev => ({ ...prev, has_binance_keys: true }));
      }
      if (dataToSend.openai_api_key) {
        setKeyStatus(prev => ({ ...prev, has_openai_key: true }));
      }

      // Clear form
      setFormData({
        binance_api_key: '',
        binance_secret_key: '',
        openai_api_key: '',
      });

      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(err.message || 'Failed to update settings');
    } finally {
      setSaving(false);
    }
  };

  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value,
    });
  };

  const KeyStatusBadge = ({ hasKey, label }) => (
    <div className={`flex items-center space-x-2 px-3 py-1.5 rounded-full text-sm ${hasKey
      ? 'bg-primary-500/10 text-primary-400'
      : 'bg-gray-800 text-gray-500'
      }`}>
      {hasKey ? (
        <CheckCircle2 className="w-4 h-4" />
      ) : (
        <XCircle className="w-4 h-4" />
      )}
      <span>{label}: {hasKey ? 'Configured' : 'Not Set'}</span>
    </div>
  );

  if (loading) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-primary-500" />
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="max-w-4xl mx-auto">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">Settings</h1>
          <p className="text-gray-400">Manage your API keys and preferences</p>
        </div>

        {/* Key Status */}
        <div className="glass-effect rounded-2xl p-6 mb-6">
          <h3 className="text-lg font-medium text-white mb-4">API Key Status</h3>
          <div className="flex flex-wrap gap-3">
            <KeyStatusBadge hasKey={keyStatus.has_binance_keys} label="Binance" />
            <KeyStatusBadge hasKey={keyStatus.has_openai_key} label="OpenAI" />
          </div>
        </div>

        <div className="glass-effect rounded-2xl p-8">
          <div className="flex items-center space-x-3 mb-6">
            <Key className="w-6 h-6 text-primary-500" />
            <h2 className="text-xl font-bold text-white">API Configuration</h2>
          </div>

          <div className="bg-blue-500/10 border border-blue-500 rounded-lg p-4 mb-6 flex items-start space-x-3">
            <AlertCircle className="w-5 h-5 text-blue-500 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-blue-400">
              <p className="font-medium mb-1">Secure Storage</p>
              <p>
                Your API keys are encrypted before storage. Leave fields empty to keep existing keys.
                Enter new values to update them.
              </p>
            </div>
          </div>

          {error && (
            <div className={`px-4 py-3 rounded-lg mb-6 ${authError
              ? 'bg-amber-500/10 border border-amber-500 text-amber-400'
              : 'bg-danger-500/10 border border-danger-500 text-danger-500'
              }`}>
              <div className="flex items-center justify-between">
                <span>{error}</span>
                {authError && (
                  <button
                    onClick={() => navigate('/login')}
                    className="ml-4 flex items-center px-3 py-1 bg-amber-500 text-black rounded-lg text-sm font-medium hover:bg-amber-400 transition-colors"
                  >
                    <RefreshCw className="w-4 h-4 mr-1" />
                    Re-login
                  </button>
                )}
              </div>
            </div>
          )}

          {success && (
            <div className="bg-primary-500/10 border border-primary-500 text-primary-500 px-4 py-3 rounded-lg mb-6">
              Settings updated successfully!
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-6">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Binance API Key
              </label>
              <input
                type="text"
                name="binance_api_key"
                value={formData.binance_api_key}
                onChange={handleChange}
                className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-primary-500 transition-colors"
                placeholder={keyStatus.has_binance_keys ? "••••••••••••••••" : "Enter your Binance API key"}
                autoComplete="off"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Binance Secret Key
              </label>
              <input
                type="password"
                name="binance_secret_key"
                value={formData.binance_secret_key}
                onChange={handleChange}
                className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-primary-500 transition-colors"
                placeholder={keyStatus.has_binance_keys ? "••••••••••••••••" : "Enter your Binance secret key"}
                autoComplete="off"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                OpenAI API Key
              </label>
              <input
                type="password"
                name="openai_api_key"
                value={formData.openai_api_key}
                onChange={handleChange}
                className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-primary-500 transition-colors"
                placeholder={keyStatus.has_openai_key ? "••••••••••••••••" : "Enter your OpenAI API key"}
                autoComplete="off"
              />
            </div>

            <button
              type="submit"
              disabled={saving}
              className="w-full bg-primary-600 hover:bg-primary-700 text-white font-semibold py-3 px-4 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center"
            >
              {saving ? (
                <>
                  <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Save className="w-5 h-5 mr-2" />
                  Save Settings
                </>
              )}
            </button>
          </form>
        </div>
      </div>
    </Layout>
  );
}
