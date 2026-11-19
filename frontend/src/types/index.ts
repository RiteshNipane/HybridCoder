// ─── Message & Conversation ───────────────────────────────────────────────────

export type MessageRole = 'user' | 'assistant' | 'system';

export interface FileChange {
  path: string;
  action: 'create' | 'edit' | 'delete';
  summary?: string;
  diff?: string;
}

export interface ToolExecution {
  id: string;
  name: string;
  args: Record<string, any>;
  output?: string;
  error?: boolean;
  timestamp: number;
}

export interface TaskStatus {
  id: string;
  phase: 'planning' | 'coding' | 'reviewing' | 'testing' | 'done' | 'error';
  label: string;
  progress?: number; // 0–100
  subTasks?: { label: string; done: boolean }[];
}

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: number;
  model?: string;
  modelMode?: ModelMode;
  streaming?: boolean;
  taskStatus?: TaskStatus;
  fileChanges?: FileChange[];
  toolExecutions?: ToolExecution[];
  error?: string;
  tokens?: {
    input: number;
    output: number;
  };
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
  workspacePath?: string;
  modelMode: ModelMode;
}

// ─── Model Modes ──────────────────────────────────────────────────────────────

export type ModelMode =
  | 'AUTO'
  | 'GEMINI'
  | 'NVIDIA'
  | 'GROK'
  | 'LOCAL'
  | 'GEMINI+LOCAL'
  | 'NVIDIA+LOCAL'
  | 'GROK+LOCAL'
  | 'ALL';

// ─── Workspace ────────────────────────────────────────────────────────────────

export interface WorkspaceFile {
  path: string;
  name: string;
  size: number;
  type: 'file' | 'directory';
}

export interface WorkspaceInfo {
  path: string;
  name: string;
  files: WorkspaceFile[];
  scannedAt?: number;
}

// ─── Usage Stats ──────────────────────────────────────────────────────────────

export interface ProviderUsage {
  requests: number;
  inputTokens: number;
  outputTokens: number;
  estimatedCostUsd: number;
}

export interface UsageStats {
  today: {
    gemini: ProviderUsage;
    grok: ProviderUsage;
    ollama: ProviderUsage;
  };
  allTime: {
    gemini: ProviderUsage;
    grok: ProviderUsage;
    ollama: ProviderUsage;
  };
  lastUpdated: number;
}

// ─── Agent Status ─────────────────────────────────────────────────────────────

export type AgentPhase =
  | 'idle'
  | 'planning'
  | 'coding'
  | 'reviewing'
  | 'testing'
  | 'escalating'
  | 'error';

export interface AgentStatus {
  phase: AgentPhase;
  label: string;
  model?: string;
  startedAt?: number;
  currentTask?: string;
}

// ─── Settings ─────────────────────────────────────────────────────────────────

export type AsciiScene =
  | 'lagoon' | 'lagoon-day' | 'lagoon-night'
  | 'dolphin' | 'manta' | 'turtle' | 'seahorse' | 'hammerhead' | 'tang'
  | 'jellyfish' | 'sharks' | 'koi' | 'robots' | 'starlings'
  | 'none';

export interface Settings {
  // API Keys
  geminiApiKey: string;
  nvidiaApiKey: string;
  grokApiKey?: string;

  // Models
  geminiModel: string;
  nvidiaModel: string;
  grokModel?: string;
  ollamaModel: string;

  // Ollama
  ollamaBaseUrl: string;
  ollamaContextSize: number;

  // Routing
  defaultModelMode: ModelMode;
  autoEscalationEnabled: boolean;
  maxEscalationRetries: number;

  // Context
  maxContextTokens: number;

  // ASCII background
  asciiScene: AsciiScene;
  asciiSpeed: number; // 1–10
  asciiPaused: boolean;

  // Theme
  accentColor: string;
}

// ─── WebSocket / Streaming ────────────────────────────────────────────────────

/** One model's turn at working on the current answer (live, from the backend). */
export interface ModelStep {
  provider: string;            // 'nvidia' | 'gemini' | 'ollama' | 'grok'
  model: string;               // e.g. 'meta/llama-3.3-70b-instruct'
  state: 'thinking' | 'generating' | 'done' | 'failed';
  error?: string;
  startedAt: number;
}

export type StreamEventType =
  | 'chunk'
  | 'done'
  | 'error'
  | 'agent_status'
  | 'file_change'
  | 'task_status'
  | 'usage';

export interface StreamEvent {
  type: StreamEventType;
  conversationId: string;
  messageId?: string;
  payload: unknown;
}

export interface ChunkPayload {
  text: string;
}

export interface DonePayload {
  model: string;
  tokens: { input: number; output: number };
}

export interface ErrorPayload {
  message: string;
  code?: string;
}
