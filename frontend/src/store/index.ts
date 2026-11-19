import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { persist, createJSONStorage } from 'zustand/middleware';
import type {
  Conversation,
  Message,
  ModelMode,
  AgentStatus,
  WorkspaceInfo,
  UsageStats,
  Settings,
  ModelStep,
} from '@/types';

// ─── Default Settings ─────────────────────────────────────────────────────────

const defaultSettings: Settings = {
  geminiApiKey: '',
  nvidiaApiKey: '',
  grokApiKey: '',
  geminiModel: 'gemini-3.5-flash-lite',
  nvidiaModel: 'meta/llama-3.3-70b-instruct',
  grokModel: 'meta/llama-3.3-70b-instruct',
  ollamaModel: 'qwen2.5-coder:7b-instruct-q4_K_M',
  ollamaBaseUrl: 'http://localhost:11434',
  ollamaContextSize: 4096,
  defaultModelMode: 'AUTO',
  autoEscalationEnabled: true,
  maxEscalationRetries: 3,
  maxContextTokens: 8192,
  asciiScene: 'lagoon',
  asciiSpeed: 1,
  asciiPaused: false,
  accentColor: '#00d4ff',
};

// ─── State Shape ──────────────────────────────────────────────────────────────

interface AppState {
  // Conversations
  conversations: Conversation[];
  activeConversationId: string | null;

  // Model mode (session-level override)
  modelMode: ModelMode;

  // Agent status
  agentStatus: AgentStatus;
  modelTrail: ModelStep[];

  // Workspace
  workspace: WorkspaceInfo | null;

  // Usage
  usageStats: UsageStats | null;

  // Settings
  settings: Settings;

  // UI
  settingsOpen: boolean;
  wsConnected: boolean;

  // ── Actions ────────────────────────────────────────────────────────────────

  // Conversations
  setConversations(conversations: Conversation[]): void;
  addConversation(conversation: Conversation): void;
  removeConversation(id: string): void;
  setActiveConversation(id: string | null): void;

  // Messages
  addMessage(conversationId: string, message: Message): void;
  /** Append text to the last assistant message (streaming) */
  appendToLastMessage(conversationId: string, text: string): void;
  /** Replace/finalise last assistant message */
  updateLastMessage(conversationId: string, patch: Partial<Message>): void;

  // Model mode
  setModelMode(mode: ModelMode): void;

  // Agent
  updateAgentStatus(status: Partial<AgentStatus>): void;
  setModelActivity(ev: { provider: string; model: string; state: ModelStep['state']; error?: string }): void;
  clearModelTrail(): void;

  // Workspace
  setWorkspace(info: WorkspaceInfo | null): void;

  // Usage
  setUsageStats(stats: UsageStats): void;

  // Settings
  updateSettings(patch: Partial<Settings>): void;

  // UI
  setSettingsOpen(open: boolean): void;
  setWsConnected(connected: boolean): void;
}

// ─── Store ────────────────────────────────────────────────────────────────────

export const useStore = create<AppState>()(
  persist(
    immer((set) => ({
      conversations: [],
      activeConversationId: null,
      modelMode: 'AUTO',
      agentStatus: { phase: 'idle', label: 'Ready' },
      modelTrail: [],
      workspace: null,
      usageStats: null,
      settings: defaultSettings,
      settingsOpen: false,
      wsConnected: false,

      // ── Conversations ──────────────────────────────────────────────────────

      setConversations(conversations) {
        set((s) => {
          s.conversations = conversations;
        });
      },

      addConversation(conversation) {
        set((s) => {
          s.conversations.unshift(conversation);
          s.activeConversationId = conversation.id;
        });
      },

      removeConversation(id) {
        set((s) => {
          s.conversations = s.conversations.filter((c) => c.id !== id);
          if (s.activeConversationId === id) {
            s.activeConversationId = s.conversations[0]?.id ?? null;
          }
        });
      },

      setActiveConversation(id) {
        set((s) => {
          s.activeConversationId = id;
        });
      },

      // ── Messages ───────────────────────────────────────────────────────────

      addMessage(conversationId, message) {
        set((s) => {
          const conv = s.conversations.find((c) => c.id === conversationId);
          if (conv) {
            conv.messages.push(message);
            conv.updatedAt = Date.now();
            // Auto-title from first user message
            if (
              message.role === 'user' &&
              conv.messages.filter((m) => m.role === 'user').length === 1
            ) {
              conv.title = message.content.slice(0, 60) + (message.content.length > 60 ? '…' : '');
            }
          }
        });
      },

      appendToLastMessage(conversationId, text) {
        set((s) => {
          const conv = s.conversations.find((c) => c.id === conversationId);
          if (!conv) return;
          const last = [...conv.messages].reverse().find((m) => m.role === 'assistant');
          if (last) {
            last.content += text;
          }
        });
      },

      updateLastMessage(conversationId, patch) {
        set((s) => {
          const conv = s.conversations.find((c) => c.id === conversationId);
          if (!conv) return;
          const idx = [...conv.messages].map((_, i) => i).reverse().find(
            (i) => conv.messages[i].role === 'assistant',
          );
          if (idx !== undefined) {
            Object.assign(conv.messages[idx], patch);
          }
        });
      },

      // ── Model mode ─────────────────────────────────────────────────────────

      setModelMode(mode) {
        set((s) => {
          s.modelMode = mode;
          const conv = s.conversations.find((c) => c.id === s.activeConversationId);
          if (conv) conv.modelMode = mode;
        });
      },

      // ── Agent ──────────────────────────────────────────────────────────────

      setModelActivity(ev) {
        set((s) => {
          const last = s.modelTrail[s.modelTrail.length - 1];
          const sameModel = last && last.provider === ev.provider && last.model === ev.model;
          const lastRunning = last && (last.state === 'thinking' || last.state === 'generating');
          if (sameModel && (lastRunning || ev.state === 'done' || ev.state === 'failed')) {
            last.state = ev.state;
            if (ev.error) last.error = ev.error;
          } else {
            s.modelTrail.push({
              provider: ev.provider,
              model: ev.model,
              state: ev.state,
              error: ev.error,
              startedAt: Date.now(),
            });
            if (s.modelTrail.length > 8) s.modelTrail.shift();
          }
        });
      },

      clearModelTrail() {
        set((s) => {
          s.modelTrail = [];
        });
      },

      updateAgentStatus(status) {
        set((s) => {
          Object.assign(s.agentStatus, status);
        });
      },

      // ── Workspace ──────────────────────────────────────────────────────────

      setWorkspace(info) {
        set((s) => {
          s.workspace = info;
        });
      },

      // ── Usage ──────────────────────────────────────────────────────────────

      setUsageStats(stats) {
        set((s) => {
          s.usageStats = stats;
        });
      },

      // ── Settings ───────────────────────────────────────────────────────────

      updateSettings(patch) {
        set((s) => {
          Object.assign(s.settings, patch);
        });
      },

      // ── UI ─────────────────────────────────────────────────────────────────

      setSettingsOpen(open) {
        set((s) => {
          s.settingsOpen = open;
        });
      },

      setWsConnected(connected) {
        set((s) => {
          s.wsConnected = connected;
        });
      },
    })),
    {
      // Conversations survive page refreshes (kept in this browser's localStorage).
      name: 'hybridcoder-conversations-v1',
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({
        conversations: s.conversations,
        activeConversationId: s.activeConversationId,
        modelMode: s.modelMode,
      }),
      // A reply that was mid-stream when the page closed can never finish: clear its cursor.
      onRehydrateStorage: () => (state) => {
        state?.conversations?.forEach((c) =>
          c.messages?.forEach((m) => {
            if (m.streaming) m.streaming = false;
          }),
        );
      },
    },
  ),
);

// ─── Selectors ────────────────────────────────────────────────────────────────

export const selectActiveConversation = (s: AppState): Conversation | undefined =>
  s.conversations.find((c) => c.id === s.activeConversationId);

export const selectActiveMessages = (s: AppState): Message[] =>
  selectActiveConversation(s)?.messages ?? [];
