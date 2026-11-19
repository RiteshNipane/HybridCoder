import React, { useEffect, useRef } from 'react';
import { Sparkles, Terminal, Code2, ShieldAlert, Cpu, Pause, Play, Waves } from 'lucide-react';
import { useStore } from '@/store';
import type { AsciiScene } from '@/types';
import { ChatMessage } from './ChatMessage';
import { ChatInput } from './ChatInput';
import { ModelActivity } from './ModelActivity';

interface ChatAreaProps {
  onSendMessage: (text: string) => void;
  onStop?: () => void;
  isStreaming?: boolean;
}

const SCENES: { id: AsciiScene; label: string }[] = [
  { id: 'lagoon', label: 'Lagoon' },
  { id: 'lagoon-day', label: 'Lagoon · Day' },
  { id: 'lagoon-night', label: 'Lagoon · Night' },
  { id: 'dolphin', label: 'Dolphins' },
  { id: 'manta', label: 'Manta Rays' },
  { id: 'turtle', label: 'Sea Turtles' },
  { id: 'seahorse', label: 'Seahorses' },
  { id: 'hammerhead', label: 'Hammerheads' },
  { id: 'tang', label: 'Tang Fish' },
  { id: 'jellyfish', label: 'Jellyfish' },
  { id: 'sharks', label: 'Sharks' },
  { id: 'koi', label: 'Koi Pond' },
  { id: 'robots', label: 'Robots' },
  { id: 'starlings', label: 'Starlings' },
  { id: 'none', label: 'Off' },
];

const examplePrompts = [
  {
    title: 'Analyze Repository',
    prompt:
      'Inspect this workspace repository. Summarize its architecture, key entry points, dependencies, and test coverage.',
    icon: Terminal,
    accent: 'from-cyan-500/20',
  },
  {
    title: 'Implement Feature',
    prompt:
      'Write an implementation plan and code for adding rate-limiting middleware to our FastAPI endpoints.',
    icon: Code2,
    accent: 'from-violet-500/20',
  },
  {
    title: 'Fix & Test Bug',
    prompt:
      'Look at the tests in this workspace. Run them, identify any failures, and fix the underlying code.',
    icon: ShieldAlert,
    accent: 'from-amber-500/20',
  },
  {
    title: 'Local Qwen Coding',
    prompt:
      'Write a clean, optimized Python function with comprehensive unit tests for token bucket rate limiting.',
    icon: Cpu,
    accent: 'from-emerald-500/20',
  },
];

export const ChatArea: React.FC<ChatAreaProps> = ({ onSendMessage, onStop, isStreaming = false }) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const activeConversationId = useStore((s) => s.activeConversationId);
  const conversations = useStore((s) => s.conversations);
  const agentStatus = useStore((s) => s.agentStatus);
  const modelMode = useStore((s) => s.modelMode);
  const wsConnected = useStore((s) => s.wsConnected);
  const asciiScene = useStore((s) => (SCENES.some((x) => x.id === s.settings.asciiScene) ? s.settings.asciiScene : 'lagoon'));
  const asciiPaused = useStore((s) => s.settings.asciiPaused);
  const updateSettings = useStore((s) => s.updateSettings);

  const activeConversation = conversations.find((c) => c.id === activeConversationId);
  const messages = activeConversation ? activeConversation.messages : [];

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, agentStatus]);

  return (
    <div className="flex-1 flex flex-col h-screen overflow-hidden relative z-10">
      {/* ── Header ─────────────────────────────────────────── */}
      <header className="h-14 border-b border-white/5 bg-[#07080d]/60 backdrop-blur-xl px-6 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <h2 className="text-sm font-semibold text-gray-100 truncate max-w-md">
            {activeConversation ? activeConversation.title : 'New Chat'}
          </h2>
          <span className="text-[10px] font-mono tracking-wider px-2 py-0.5 rounded-full bg-cyan-400/10 text-cyan-300 border border-cyan-400/20">
            {modelMode}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* Background scene switcher */}
          <label className="flex items-center gap-1.5 text-[11px] text-gray-400 bg-white/5 border border-white/10 rounded-lg pl-2 pr-1 py-1 hover:border-cyan-400/40 transition-colors">
            <Waves className="w-3.5 h-3.5 text-cyan-400" />
            <select
              value={asciiScene}
              onChange={(e) => updateSettings({ asciiScene: e.target.value as AsciiScene })}
              className="bg-transparent text-gray-200 text-[11px] font-mono outline-none cursor-pointer"
            >
              {SCENES.map((s) => (
                <option key={s.id} value={s.id} className="bg-[#0d1117]">
                  {s.label}
                </option>
              ))}
            </select>
          </label>

          <button
            onClick={() => updateSettings({ asciiPaused: !asciiPaused })}
            title={asciiPaused ? 'Resume background' : 'Pause background'}
            className="p-1.5 rounded-lg bg-white/5 border border-white/10 text-gray-300 hover:text-cyan-300 hover:border-cyan-400/40 transition-colors"
          >
            {asciiPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
          </button>

          <div className="flex items-center gap-1.5 pl-2 ml-1 border-l border-white/10">
            <span
              className={`w-2 h-2 rounded-full ${
                wsConnected ? 'bg-emerald-400 animate-pulse' : 'bg-red-500'
              }`}
            />
            <span className="text-[11px] font-mono text-gray-400">
              {wsConnected ? 'Connected' : 'Offline'}
            </span>
          </div>
        </div>
      </header>

      {/* ── Agent status banner ────────────────────────────── */}
      {agentStatus && agentStatus.phase !== 'idle' && (
        <div className="bg-cyan-400/10 border-b border-cyan-400/20 px-6 py-2 flex items-center justify-between backdrop-blur-md animate-fade-in">
          <div className="flex items-center gap-2 text-xs text-cyan-300">
            <Sparkles className="w-4 h-4 animate-spin text-cyan-400" />
            <span className="font-mono">{agentStatus.label || 'Agent thinking...'}</span>
          </div>
          <ModelActivity />
        </div>
      )}

      {/* ── Messages ───────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto w-full px-6 py-6 min-h-full flex flex-col">
          {messages.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center text-center py-10 animate-fade-in">
              <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-cyan-400/25 to-violet-500/20 border border-cyan-400/30 flex items-center justify-center mb-5 text-cyan-300 shadow-[0_0_40px_-8px_rgba(0,212,255,0.5)]">
                <Sparkles className="w-7 h-7" />
              </div>
              <h1 className="text-3xl font-bold tracking-tight bg-gradient-to-r from-white via-cyan-200 to-violet-300 bg-clip-text text-transparent mb-2">
                Hybrid Coding Agent
              </h1>
              <p className="text-sm text-gray-400 mb-10 max-w-md leading-relaxed">
                Uses NVIDIA NIM as the default cloud expert, Gemini as a fallback, and local Qwen on Ollama for code changes.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full text-left">
                {examplePrompts.map((item) => (
                  <button
                    key={item.title}
                    onClick={() => onSendMessage(item.prompt)}
                    className={`relative overflow-hidden p-4 rounded-2xl bg-white/[0.04] border border-white/10 hover:border-cyan-400/50 hover:-translate-y-0.5 hover:shadow-[0_8px_30px_-12px_rgba(0,212,255,0.45)] transition-all duration-200 text-left group backdrop-blur-md`}
                  >
                    <div
                      className={`absolute inset-0 bg-gradient-to-br ${item.accent} to-transparent opacity-0 group-hover:opacity-100 transition-opacity`}
                    />
                    <div className="relative">
                      <div className="flex items-center gap-2 text-cyan-300 mb-1.5 font-medium text-sm">
                        <item.icon className="w-4 h-4 group-hover:scale-110 transition-transform" />
                        <span>{item.title}</span>
                      </div>
                      <p className="text-xs text-gray-400 line-clamp-2 leading-relaxed">
                        {item.prompt}
                      </p>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg) => <ChatMessage key={msg.id} message={msg} />)
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* ── Input ──────────────────────────────────────────── */}
      <footer className="shrink-0 bg-gradient-to-t from-[#07080d] via-[#07080d]/85 to-transparent pt-2">
        <ChatInput onSend={onSendMessage} onStop={onStop} isStreaming={isStreaming} />
      </footer>
    </div>
  );
};
