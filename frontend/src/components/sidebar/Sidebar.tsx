import { useActiveModel } from '@/hooks/useActiveModel';
import React, { useEffect, useState } from 'react';
import {
  Plus,
  MessageSquare,
  Trash2,
  FolderTree,
  Settings,
  Cpu,
  Zap,
  Activity,
  Layers,
  Terminal as TerminalIcon,
  Folder,
} from 'lucide-react';
import { useStore } from '@/store';
import type { Conversation } from '@/types';
import { FileBrowser } from '../file-browser/FileBrowser';
import { TerminalPanel } from '../terminal/TerminalPanel';

export const Sidebar: React.FC = () => {
  const conversations = useStore((s) => s.conversations);
  const activeConversationId = useStore((s) => s.activeConversationId);
  const setActiveConversation = useStore((s) => s.setActiveConversation);
  const addConversation = useStore((s) => s.addConversation);
  const removeConversation = useStore((s) => s.removeConversation);
  const workspace = useStore((s) => s.workspace);
  const setSettingsOpen = useStore((s) => s.setSettingsOpen);

  const [activeTab, setActiveTab] = useState<'chats' | 'files'>('chats');
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [systemStatus, setSystemStatus] = useState<any>(null);
  const [gpu, setGpu] = useState<any>(null);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    try {
      const saved = Number(localStorage.getItem('hybridcoder-sidebar-width'));
      return Number.isFinite(saved) && saved >= 220 && saved <= 480 ? saved : 256;
    } catch {
      return 256;
    }
  });
  const [isResizing, setIsResizing] = useState(false);
  const activeModel = useActiveModel();

  useEffect(() => {
    if (!isResizing) return;
    const resize = (event: PointerEvent) => {
      setSidebarWidth(Math.max(220, Math.min(480, event.clientX)));
    };
    const finish = () => setIsResizing(false);
    window.addEventListener('pointermove', resize);
    window.addEventListener('pointerup', finish);
    window.addEventListener('pointercancel', finish);
    return () => {
      window.removeEventListener('pointermove', resize);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', finish);
    };
  }, [isResizing]);

  useEffect(() => {
    try {
      localStorage.setItem('hybridcoder-sidebar-width', String(sidebarWidth));
    } catch {
      // Keep resizing usable when browser storage is unavailable.
    }
  }, [sidebarWidth]);

  const adjustSidebarWidth = (delta: number) => {
    setSidebarWidth((width) => Math.max(220, Math.min(480, width + delta)));
  };

  // Live GPU stats: poll every 2s (only while the tab is visible)
  useEffect(() => {
    let cancelled = false;
    const fetchGpu = async () => {
      if (document.hidden) return;
      try {
        const res = await fetch('http://localhost:8000/api/gpu');
        if (res.ok && !cancelled) setGpu(await res.json());
      } catch {
        // backend might be starting
      }
    };
    fetchGpu();
    const interval = setInterval(fetchGpu, 2000);
    const onVisible = () => { if (!document.hidden) fetchGpu(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      cancelled = true;
      clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  // Poll system status every 10 seconds
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch('http://localhost:8000/api/status');
        if (res.ok) {
          const data = await res.json();
          setSystemStatus(data);
        }
      } catch {
        // backend might be starting
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleNewChat = () => {
    const newConv: Conversation = {
      id: `conv_${Date.now()}`,
      title: 'New Conversation',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      messages: [],
      modelMode: 'AUTO',
    };
    addConversation(newConv);
    setActiveConversation(newConv.id);
  };

  return (
    <aside
      style={{ width: sidebarWidth }}
      className={`h-screen border-r border-gray-800/60 bg-gray-950/70 backdrop-blur-xl flex flex-col justify-between shrink-0 relative z-20 ${isResizing ? 'select-none' : ''}`}
    >
      {/* Top Section */}
      <div className="flex flex-col flex-1 overflow-hidden p-3">
        {/* Brand */}
        <div className="flex items-center gap-2.5 px-2 py-3 mb-2 border-b border-gray-800/60">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-cyan-600 to-blue-600 flex items-center justify-center text-white shadow-md shadow-cyan-950">
            <Layers className="w-4 h-4" />
          </div>
          <div>
            <h1 className="text-sm font-bold text-gray-100 tracking-tight">HybridCoder</h1>
            <p className="text-[10px] font-mono text-cyan-400">Autonomous 3-Model AI</p>
          </div>
        </div>

        {/* Navigation Tabs (Chats vs Files) */}
        <div className="flex rounded-lg bg-gray-900/80 p-0.5 mb-3 border border-gray-800">
          <button
            onClick={() => setActiveTab('chats')}
            className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md text-xs font-medium transition-all ${
              activeTab === 'chats'
                ? 'bg-cyan-600/30 text-cyan-300 border border-cyan-500/40 shadow-sm'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            <MessageSquare className="w-3.5 h-3.5" />
            <span>Chats</span>
          </button>
          <button
            onClick={() => setActiveTab('files')}
            className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md text-xs font-medium transition-all ${
              activeTab === 'files'
                ? 'bg-cyan-600/30 text-cyan-300 border border-cyan-500/40 shadow-sm'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            <FolderTree className="w-3.5 h-3.5" />
            <span>Files</span>
          </button>
        </div>

        {activeTab === 'chats' ? (
          <>
            {/* New Chat Button */}
            <button
              onClick={handleNewChat}
              className="flex items-center justify-center gap-2 w-full py-2 px-3 rounded-lg bg-cyan-600/20 hover:bg-cyan-600/30 border border-cyan-500/30 text-cyan-300 text-xs font-medium transition-all mb-3 shadow-sm"
            >
              <Plus className="w-4 h-4" />
              <span>New Conversation</span>
            </button>

            {/* History List */}
            <div className="flex-1 overflow-y-auto space-y-1 pr-1">
              <div className="px-2 py-1 text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
                Conversations
              </div>
              {conversations.length === 0 ? (
                <div className="px-2 py-4 text-center text-xs text-gray-500">
                  No conversations yet
                </div>
              ) : (
                conversations.map((c) => (
                  <div
                    key={c.id}
                    onClick={() => setActiveConversation(c.id)}
                    className={`group flex items-center justify-between px-2.5 py-2 rounded-lg text-xs cursor-pointer transition-all ${
                      c.id === activeConversationId
                        ? 'bg-gray-800/80 text-cyan-300 font-medium border border-gray-700/60 shadow-sm'
                        : 'text-gray-400 hover:bg-gray-900/60 hover:text-gray-200'
                    }`}
                  >
                    <div className="flex items-center gap-2 truncate">
                      <MessageSquare className="w-3.5 h-3.5 shrink-0 opacity-70" />
                      <span className="truncate">{c.title}</span>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        removeConversation(c.id);
                      }}
                      className="opacity-0 group-hover:opacity-100 p-1 hover:text-red-400 transition-opacity"
                      title="Delete chat"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                ))
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 overflow-hidden">
            <FileBrowser />
          </div>
        )}

        {/* Workspace Info */}
        {workspace && (
          <div className="mt-3 p-2.5 rounded-lg bg-black/40 border border-gray-800/80 text-xs">
            <div className="flex items-center gap-1.5 text-cyan-400 mb-1 font-medium text-[11px]">
              <FolderTree className="w-3.5 h-3.5" />
              <span>Workspace</span>
            </div>
            <p className="font-mono text-[10px] text-gray-300 truncate" title={workspace.path}>
              {workspace.path}
            </p>
          </div>
        )}
      </div>

      {/* Bottom Status & Settings Section */}
      <div className="p-3 border-t border-gray-800/60 bg-black/20">
        {/* Model Status Indicators */}
        <div className="space-y-1.5 mb-3 px-1 text-[11px]">
          <div className="flex items-center justify-between text-gray-400">
            <span className="flex items-center gap-1.5">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  systemStatus?.providers?.gemini?.configured ? 'bg-blue-400' : 'bg-gray-600'
                }`}
              />
              <span className="truncate max-w-[150px]" title={systemStatus?.providers?.gemini?.model || 'Gemini'}>
                {systemStatus?.providers?.gemini?.model || 'Gemini'}
              </span>
            </span>
            <span className={`font-mono text-[10px] ${activeModel?.provider === 'gemini' ? 'text-blue-300 animate-pulse' : 'text-gray-500'}`}>
              {activeModel?.provider === 'gemini'
                ? 'Working…'
                : systemStatus?.providers?.gemini?.configured ? 'Active' : 'Not Set'}
            </span>
          </div>

          <div className="flex items-center justify-between text-gray-400">
            <span className="flex items-center gap-1.5">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  systemStatus?.providers?.grok?.configured ? 'bg-purple-400' : 'bg-gray-600'
                }`}
              />
              <span className="truncate max-w-[150px]" title={systemStatus?.providers?.nvidia?.model || 'NVIDIA NIM'}>
                {systemStatus?.providers?.nvidia?.model || 'NVIDIA NIM'}
              </span>
            </span>
            <span className={`font-mono text-[10px] ${activeModel?.provider === 'nvidia' || activeModel?.provider === 'grok' ? 'text-lime-300 animate-pulse' : 'text-gray-500'}`}>
              {activeModel?.provider === 'nvidia' || activeModel?.provider === 'grok'
                ? 'Working…'
                : systemStatus?.providers?.grok?.configured ? 'Active' : 'Not Set'}
            </span>
          </div>

          <div className="flex items-center justify-between text-gray-400">
            <span className="flex items-center gap-1.5">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  systemStatus?.providers?.ollama?.running ? 'bg-emerald-400' : 'bg-red-500'
                }`}
              />
              <span className="truncate max-w-[150px]" title={systemStatus?.providers?.ollama?.model || 'Local Ollama'}>
                {systemStatus?.providers?.ollama?.model || 'Local Ollama'}
              </span>
            </span>
            <span className={`font-mono text-[10px] ${activeModel?.provider === 'ollama' ? 'text-emerald-300 animate-pulse' : 'text-gray-500'}`}>
              {activeModel?.provider === 'ollama'
                ? 'Working…'
                : systemStatus?.providers?.ollama?.running ? 'Ollama Online' : 'Offline'}
            </span>
          </div>

          {gpu?.available && (() => {
            const pct = gpu.vram_total_mb ? Math.round((gpu.vram_used_mb / gpu.vram_total_mb) * 100) : 0;
            const bar = pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-amber-400' : 'bg-emerald-400';
            const loaded = (gpu.ollama_loaded || [])[0];
            return (
              <div className="pt-2 border-t border-gray-800/40 space-y-1.5" title={gpu.name}>
                <div className="flex items-center justify-between text-gray-400">
                  <span className="flex items-center gap-1.5">
                    <Cpu className="w-3 h-3 text-cyan-400" />
                    VRAM
                  </span>
                  <span className="font-mono text-[10px] text-gray-300">
                    {(gpu.vram_used_mb / 1024).toFixed(1)} / {(gpu.vram_total_mb / 1024).toFixed(1)} GB · {pct}%
                  </span>
                </div>
                <div className="h-1.5 w-full rounded-full bg-white/10 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-700 ${bar}`}
                    style={{ width: `${Math.min(pct, 100)}%` }}
                  />
                </div>
                <div className="flex items-center justify-between font-mono text-[10px] text-gray-500">
                  <span>
                    GPU {gpu.utilization_pct ?? '–'}%
                    {gpu.temperature_c != null ? ` · ${gpu.temperature_c}°C` : ''}
                  </span>
                  <span className="truncate ml-2">
                    {loaded
                      ? `${String(loaded.name).split(':')[0]} · ${(loaded.vram_mb / 1024).toFixed(1)} GB`
                      : 'no model loaded'}
                  </span>
                </div>
              </div>
            );
          })()}
        </div>

        {/* Terminal & Settings buttons */}
        <div className="space-y-1.5">
          <button
            onClick={() => setTerminalOpen(!terminalOpen)}
            className="flex items-center justify-between w-full px-2.5 py-2 rounded-lg bg-gray-900/60 hover:bg-gray-800/70 border border-gray-800 text-xs text-gray-300 transition-colors"
          >
            <div className="flex items-center gap-2">
              <TerminalIcon className="w-3.5 h-3.5 text-amber-400" />
              <span>Workspace Terminal</span>
            </div>
            <span className="text-[10px] font-mono text-amber-400">Run</span>
          </button>

          <button
            onClick={() => setSettingsOpen(true)}
            className="flex items-center justify-between w-full px-2.5 py-2 rounded-lg bg-gray-900/60 hover:bg-gray-800/70 border border-gray-800 text-xs text-gray-300 transition-colors"
          >
            <div className="flex items-center gap-2">
              <Settings className="w-3.5 h-3.5 text-gray-400" />
              <span>Settings & Models</span>
            </div>
            <span className="text-[10px] font-mono text-cyan-400">Config</span>
          </button>
        </div>
      </div>

      {/* Terminal Runner Drawer */}
      <TerminalPanel isOpen={terminalOpen} onClose={() => setTerminalOpen(false)} />
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize sidebar"
        aria-valuemin={220}
        aria-valuemax={480}
        aria-valuenow={sidebarWidth}
        tabIndex={0}
        onPointerDown={(event) => {
          event.preventDefault();
          setIsResizing(true);
        }}
        onDoubleClick={() => setSidebarWidth(256)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowLeft') adjustSidebarWidth(-16);
          if (event.key === 'ArrowRight') adjustSidebarWidth(16);
        }}
        title="Drag to resize · double-click to reset"
        className="absolute top-0 right-0 z-30 h-full w-1 cursor-col-resize touch-none hover:bg-cyan-400/40 focus:bg-cyan-400/50 focus:outline-none transition-colors"
      />
    </aside>
  );
};

