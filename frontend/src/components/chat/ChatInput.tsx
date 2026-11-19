import React, { useState, useRef, useEffect } from 'react';
import { Send, Square, FolderGit2, Trash2, Cpu } from 'lucide-react';
import { useStore } from '@/store';
import { api } from '@/services/api';
import type { ModelMode } from '@/types';

interface ChatInputProps {
  onSend: (message: string) => void;
  onStop?: () => void;
  isStreaming?: boolean;
}

const MODEL_MODES: { value: ModelMode; label: string; desc: string }[] = [
  { value: 'AUTO', label: 'AUTO', desc: 'NVIDIA NIM cloud expert + Local Qwen; Gemini fallback' },
  { value: 'GEMINI', label: 'GEMINI', desc: 'Google Gemini cloud expert' },
  { value: 'NVIDIA', label: 'NVIDIA NIM', desc: 'NVIDIA NIM cloud expert for answers, planning, architecture, and review' },
  { value: 'LOCAL', label: 'LOCAL QWEN', desc: 'Qwen2.5-Coder 7B Q4 (Offline GPU/CPU)' },
  { value: 'GEMINI+LOCAL', label: 'GEMINI + LOCAL', desc: 'Gemini Architecture + Local Implementation' },
  { value: 'NVIDIA+LOCAL', label: 'NVIDIA + LOCAL', desc: 'NVIDIA Architecture + Local Implementation' },
  { value: 'ALL', label: 'ALL MODELS', desc: 'NVIDIA NIM planning + Local Qwen; Gemini fallback' },
];

export const ChatInput: React.FC<ChatInputProps> = ({ onSend, onStop, isStreaming = false }) => {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const modelMode = useStore((s) => s.modelMode);
  const setModelMode = useStore((s) => s.setModelMode);
  const workspace = useStore((s) => s.workspace);
  const setWorkspace = useStore((s) => s.setWorkspace);
  const activeConversationId = useStore((s) => s.activeConversationId);
  const removeConversation = useStore((s) => s.removeConversation);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  }, [input]);

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!input.trim() || isStreaming) return;
    onSend(input.trim());
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleSelectWorkspace = async () => {
    try {
      const data = await api.selectWorkspace();
      if (data.cancelled || !data.root) return;
      setWorkspace({
        path: data.root,
        name: data.root.split(/[\\/]/).pop() || data.root,
        files: (data.files || []).map((f) => ({ path: f, name: f.split(/[\\/]/).pop() || f, size: 0, type: 'file' })),
      });
    } catch (err: any) {
      alert(`Could not open/select workspace: ${err.message}`);
    }
  };

  const handleClear = () => {
    if (activeConversationId && confirm('Clear this conversation?')) {
      removeConversation(activeConversationId);
    }
  };

  return (
    <div className="w-full max-w-4xl mx-auto p-3">
      {/* Workspace & Controls Bar */}
      <div className="flex items-center justify-between gap-2 mb-2 px-1 text-xs text-gray-400">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleSelectWorkspace}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-gray-900/60 border border-gray-800 hover:border-gray-700 hover:text-cyan-300 transition-colors backdrop-blur-sm"
          >
            <FolderGit2 className="w-3.5 h-3.5 text-cyan-400" />
            <span className="truncate max-w-[220px]">
              {workspace ? workspace.name : 'Select Workspace'}
            </span>
          </button>
        </div>

        <div className="flex items-center gap-2">
          {/* Model Selector */}
          <div className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-gray-900/60 border border-gray-800 backdrop-blur-sm">
            <Cpu className="w-3.5 h-3.5 text-cyan-400" />
            <select
              value={modelMode}
              onChange={(e) => setModelMode(e.target.value as ModelMode)}
              className="bg-transparent text-gray-200 text-xs font-mono focus:outline-none cursor-pointer"
            >
              {MODEL_MODES.map((m) => (
                <option key={m.value} value={m.value} className="bg-gray-900 text-gray-200">
                  {m.label}
                </option>
              ))}
            </select>
          </div>

          {activeConversationId && (
            <button
              type="button"
              onClick={handleClear}
              title="Clear conversation"
              className="p-1.5 rounded-md bg-gray-900/60 border border-gray-800 hover:text-red-400 hover:border-red-900/50 transition-colors"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Input Box */}
      <div className="relative rounded-xl border border-gray-800/80 bg-gray-900/70 backdrop-blur-md shadow-2xl focus-within:border-cyan-500/50 focus-within:ring-1 focus-within:ring-cyan-500/30 transition-all">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask anything, request a feature, bug fix, or refactor across your workspace..."
          rows={1}
          disabled={isStreaming}
          className="w-full bg-transparent px-4 py-3.5 pr-14 text-sm text-gray-100 placeholder-gray-500 focus:outline-none resize-none max-h-[180px]"
        />

        <div className="absolute right-2.5 bottom-2.5">
          {isStreaming ? (
            <button
              type="button"
              onClick={onStop}
              className="p-2 rounded-lg bg-red-600/80 hover:bg-red-500 text-white transition-all shadow-md"
              title="Stop Generation"
            >
              <Square className="w-4 h-4" />
            </button>
          ) : (
            <button
              type="button"
              onClick={() => handleSubmit()}
              disabled={!input.trim()}
              className="p-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 disabled:opacity-40 disabled:hover:bg-cyan-600 text-white transition-all shadow-md"
              title="Send (Enter)"
            >
              <Send className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between mt-1.5 px-2 text-[10px] text-gray-500">
        <span>Shift + Enter for new line • Enter to send</span>
        <span>Default cloud expert: NVIDIA NIM • Gemini fallback • Local Qwen implementation</span>
      </div>
    </div>
  );
};

