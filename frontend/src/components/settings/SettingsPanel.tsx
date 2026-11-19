import React, { useState, useEffect } from 'react';
import { X, Key, Cpu, Sparkles, Activity, Shield, Save } from 'lucide-react';
import { useStore } from '@/store';

export const SettingsPanel: React.FC = () => {
  const settingsOpen = useStore((s) => s.settingsOpen);
  const setSettingsOpen = useStore((s) => s.setSettingsOpen);
  const settings = useStore((s) => s.settings);
  const updateSettings = useStore((s) => s.updateSettings);

  const [geminiKey, setGeminiKey] = useState(settings.geminiApiKey || '');
  const [grokKey, setGrokKey] = useState(settings.grokApiKey || '');
  const [geminiModel, setGeminiModel] = useState('gemini-3.5-flash-lite');
  const [grokModel, setGrokModel] = useState(settings.grokModel || 'grok-4.7');
  const [ollamaModel, setOllamaModel] = useState(
    settings.ollamaModel === 'qwen2.5-coder:7b'
      ? 'qwen2.5-coder:7b-instruct-q4_K_M'
      : settings.ollamaModel || 'qwen2.5-coder:7b-instruct-q4_K_M',
  );
  const [asciiScene, setAsciiScene] = useState(settings.asciiScene || 'lagoon');
  const [asciiSpeed, setAsciiSpeed] = useState(settings.asciiSpeed ?? 1);
  const [asciiPaused, setAsciiPaused] = useState(settings.asciiPaused || false);
  const [autoEscalation, setAutoEscalation] = useState(settings.autoEscalationEnabled ?? true);
  const [usageData, setUsageData] = useState<any>(null);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  useEffect(() => {
    if (settingsOpen) {
      // Fetch usage stats
      fetch('http://localhost:8000/api/usage')
        .then((r) => r.json())
        .then((d) => setUsageData(d))
        .catch(() => {});
    }
  }, [settingsOpen]);

  if (!settingsOpen) return null;

  const handleSave = async () => {
    updateSettings({
      geminiApiKey: geminiKey,
      grokApiKey: grokKey,
      geminiModel,
      grokModel,
      ollamaModel,
      asciiScene: asciiScene as any,
      asciiSpeed,
      asciiPaused,
      autoEscalationEnabled: autoEscalation,
    });

    try {
      const res = await fetch('http://localhost:8000/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          gemini_api_key: geminiKey || undefined,
          xai_api_key: grokKey || undefined,
          gemini_model: geminiModel,
          xai_model: grokModel,
          ollama_model: ollamaModel,
          escalation_enabled: autoEscalation,
        }),
      });
      if (res.ok) {
        setSaveStatus('Settings updated successfully!');
        setTimeout(() => setSaveStatus(null), 3000);
      }
    } catch (e: any) {
      setSaveStatus(`Error: ${e.message}`);
    }
  };

  const maskKey = (key: string) => {
    if (!key) return '';
    if (key.length <= 4) return '****';
    return `••••••••••••${key.slice(-4)}`;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-md animate-fade-in">
      <div className="relative w-full max-w-2xl max-h-[90vh] bg-gray-950 border border-gray-800 rounded-2xl shadow-2xl flex flex-col overflow-hidden text-gray-200">
        {/* Modal Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 bg-gray-900/40">
          <div className="flex items-center gap-2">
            <Cpu className="w-5 h-5 text-cyan-400" />
            <h2 className="text-base font-semibold text-gray-100">System Configuration</h2>
          </div>
          <button
            onClick={() => setSettingsOpen(false)}
            className="p-1 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {saveStatus && (
            <div className="p-3 rounded-lg bg-emerald-950/60 border border-emerald-800 text-xs text-emerald-300">
              {saveStatus}
            </div>
          )}

          {/* Cloud API Keys */}
          <div className="space-y-4">
            <h3 className="text-xs font-semibold text-cyan-400 uppercase tracking-wider flex items-center gap-1.5">
              <Key className="w-3.5 h-3.5" />
              <span>Model API Credentials</span>
            </h3>

            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1">
                Google Gemini API Key
              </label>
              <input
                type="password"
                value={geminiKey}
                onChange={(e) => setGeminiKey(e.target.value)}
                placeholder="AIzaSy... (leave blank if set via .env)"
                className="w-full px-3 py-2 rounded-lg bg-gray-900 border border-gray-800 focus:border-cyan-500 text-xs font-mono text-gray-100 focus:outline-none"
              />
              <span className="text-[10px] text-gray-500 mt-1 block">
                Stored securely on backend; never committed or exposed to browser JS.
              </span>
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1">
                xAI Grok API Key
              </label>
              <input
                type="password"
                value={grokKey}
                onChange={(e) => setGrokKey(e.target.value)}
                placeholder="xai-... (leave blank if set via .env)"
                className="w-full px-3 py-2 rounded-lg bg-gray-900 border border-gray-800 focus:border-cyan-500 text-xs font-mono text-gray-100 focus:outline-none"
              />
            </div>
          </div>

          {/* Model Selection */}
          <div className="space-y-4 border-t border-gray-800/80 pt-4">
            <h3 className="text-xs font-semibold text-cyan-400 uppercase tracking-wider flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5" />
              <span>Active Models</span>
            </h3>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">Gemini Model</label>
                <select
                  value={geminiModel}
                  onChange={(e) => setGeminiModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-gray-900 border border-gray-800 text-xs font-mono text-gray-100 focus:outline-none"
                >
                  <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite (Recommended / lower demand)</option>
                  <option value="gemini-3.8-flash">gemini-3.8-flash (Higher capability)</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">Grok Model</label>
                <select
                  value={grokModel}
                  onChange={(e) => setGrokModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-gray-900 border border-gray-800 text-xs font-mono text-gray-100 focus:outline-none"
                >
                  <option value="grok-4.7">grok-4.7 (Flagship)</option>
                  <option value="grok-beta">grok-beta</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">Local Ollama Model</label>
                <input
                  type="text"
                  value={ollamaModel}
                  onChange={(e) => setOllamaModel(e.target.value)}
                  placeholder="qwen2.5-coder:7b-instruct-q4_K_M"
                  className="w-full px-3 py-2 rounded-lg bg-gray-900 border border-gray-800 text-xs font-mono text-gray-100 focus:outline-none"
                />
              </div>

              <div className="flex items-center gap-2 pt-6">
                <input
                  type="checkbox"
                  id="escalateCheck"
                  checked={autoEscalation}
                  onChange={(e) => setAutoEscalation(e.target.checked)}
                  className="rounded bg-gray-900 border-gray-800 text-cyan-500 focus:ring-0 cursor-pointer"
                />
                <label htmlFor="escalateCheck" className="text-xs text-gray-300 cursor-pointer">
                  Auto-Escalation on Test Failure
                </label>
              </div>
            </div>
          </div>

          {/* ASCII Things Background Settings */}
          <div className="space-y-4 border-t border-gray-800/80 pt-4">
            <h3 className="text-xs font-semibold text-cyan-400 uppercase tracking-wider flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5" />
              <span>ASCII Background (ascii-things)</span>
            </h3>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">Scene</label>
                <select
                  value={asciiScene}
                  onChange={(e) => setAsciiScene(e.target.value as any)}
                  className="w-full px-3 py-2 rounded-lg bg-gray-900 border border-gray-800 text-xs text-gray-100 focus:outline-none"
                >
                  <option value="lagoon">Lagoon (Default)</option>
                  <option value="lagoon-day">Lagoon · Day Reef</option>
                  <option value="lagoon-night">Lagoon · Night</option>
                  <option value="dolphin">Lagoon · Dolphins</option>
                  <option value="manta">Lagoon · Manta Rays</option>
                  <option value="turtle">Lagoon · Sea Turtles</option>
                  <option value="seahorse">Lagoon · Seahorses</option>
                  <option value="hammerhead">Lagoon · Hammerheads</option>
                  <option value="tang">Lagoon · Tang Fish</option>
                  <option value="jellyfish">Jellyfish (Deep Sea Bloom)</option>
                  <option value="sharks">Sharks (Ocean Predators)</option>
                  <option value="koi">Koi Pond</option>
                  <option value="robots">Robots (Factory Floor)</option>
                  <option value="starlings">Starlings (Murmuration)</option>
                  <option value="none">Disabled (Solid Dark)</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1">
                  Speed ({asciiSpeed}x)
                </label>
                <input
                  type="range"
                  min="0.2"
                  max="3"
                  step="0.2"
                  value={asciiSpeed}
                  onChange={(e) => setAsciiSpeed(parseFloat(e.target.value))}
                  className="w-full mt-2 accent-cyan-500 cursor-pointer"
                />
              </div>
            </div>

            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="pauseCheck"
                checked={asciiPaused}
                onChange={(e) => setAsciiPaused(e.target.checked)}
                className="rounded bg-gray-900 border-gray-800 text-cyan-500 focus:ring-0 cursor-pointer"
              />
              <label htmlFor="pauseCheck" className="text-xs text-gray-300 cursor-pointer">
                Pause ASCII Background Animation (Saves GPU/CPU)
              </label>
            </div>
          </div>

          {/* Usage Stats Summary */}
          {usageData && (
            <div className="border-t border-gray-800/80 pt-4">
              <h3 className="text-xs font-semibold text-cyan-400 uppercase tracking-wider flex items-center gap-1.5 mb-2">
                <Activity className="w-3.5 h-3.5" />
                <span>API Usage & Cost Tracker</span>
              </h3>
              <div className="grid grid-cols-3 gap-3">
                <div className="p-3 rounded-lg bg-gray-900/60 border border-gray-800 text-center">
                  <div className="text-[10px] text-gray-400">Total Requests</div>
                  <div className="text-base font-bold text-gray-100 font-mono mt-0.5">
                    {usageData.total_requests || 0}
                  </div>
                </div>
                <div className="p-3 rounded-lg bg-gray-900/60 border border-gray-800 text-center">
                  <div className="text-[10px] text-gray-400">Total Tokens</div>
                  <div className="text-base font-bold text-gray-100 font-mono mt-0.5">
                    {usageData.total_tokens || 0}
                  </div>
                </div>
                <div className="p-3 rounded-lg bg-gray-900/60 border border-gray-800 text-center">
                  <div className="text-[10px] text-gray-400">Estimated Cost</div>
                  <div className="text-base font-bold text-emerald-400 font-mono mt-0.5">
                    ${(usageData.total_cost_usd || 0).toFixed(4)}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="px-6 py-3 border-t border-gray-800 bg-gray-900/40 flex items-center justify-end gap-2">
          <button
            onClick={() => setSettingsOpen(false)}
            className="px-4 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs text-gray-300 transition-colors"
          >
            Close
          </button>
          <button
            onClick={handleSave}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-xs font-medium text-white transition-all shadow-md"
          >
            <Save className="w-3.5 h-3.5" />
            <span>Save Settings</span>
          </button>
        </div>
      </div>
    </div>
  );
};
