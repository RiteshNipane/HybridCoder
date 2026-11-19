import type {
  Conversation,
  Message,
  ModelMode,
  StreamEvent,
  UsageStats,
  AgentStatus,
  WorkspaceInfo,
} from '@/types';

// ─── Event Emitter ────────────────────────────────────────────────────────────

type EventHandler = (event: StreamEvent) => void;

class StreamEventEmitter {
  private handlers: Set<EventHandler> = new Set();

  on(handler: EventHandler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  emit(event: StreamEvent) {
    this.handlers.forEach((h) => h(event));
  }
}

export const streamEvents = new StreamEventEmitter();

// ─── WebSocket Manager ────────────────────────────────────────────────────────

class WebSocketManager {
  private ws: WebSocket | null = null;
  private url: string = '';
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30_000;
  private shouldReconnect = true;
  private activeGenerationConversationId: string | null = null;
  public connected = false;
  private connectionListeners: Set<(connected: boolean) => void> = new Set();

  connect(url: string) {
    this.url = url;
    this.shouldReconnect = true;
    this._connect();
  }

  private _connect() {
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.close();
    }

    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.connected = true;
      this.reconnectDelay = 1000;
      this._notifyListeners(true);
    };

    this.ws.onmessage = (ev) => {
      try {
        const event: StreamEvent = JSON.parse(ev.data as string);
        streamEvents.emit(event);
      } catch {
        // ignore malformed
      }
    };

    this.ws.onclose = () => {
      this.connected = false;
      this.activeGenerationConversationId = null;
      this._notifyListeners(false);
      if (this.shouldReconnect) {
        this._scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      // close will fire next
    };
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      if (this.shouldReconnect) this._connect();
    }, this.reconnectDelay);
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
  }

  disconnect() {
    this.shouldReconnect = false;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
    }
    this.ws = null;
    this.connected = false;
    this.activeGenerationConversationId = null;
    this._notifyListeners(false);
  }

  /** Returns false if the socket isn't open (message NOT sent). */
  send(data: unknown): boolean {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
      return true;
    }
    return false;
  }

  setActiveGeneration(conversationId: string) {
    this.activeGenerationConversationId = conversationId;
  }

  stopActiveGeneration(): boolean {
    const conversationId = this.activeGenerationConversationId;
    return conversationId
      ? this.send({ type: 'stop', conversationId })
      : false;
  }

  finishGeneration(conversationId: string) {
    if (this.activeGenerationConversationId === conversationId) {
      this.activeGenerationConversationId = null;
    }
  }

  onConnectionChange(listener: (connected: boolean) => void) {
    this.connectionListeners.add(listener);
    return () => this.connectionListeners.delete(listener);
  }

  private _notifyListeners(connected: boolean) {
    this.connectionListeners.forEach((l) => l(connected));
  }
}

export const wsManager = new WebSocketManager();

// ─── REST API Helpers ─────────────────────────────────────────────────────────

const BASE = '/api';

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${method} ${path} failed (${res.status}): ${text}`);
  }
  return res.json() as Promise<T>;
}

// ─── API Surface ──────────────────────────────────────────────────────────────

export const api = {
  // Conversations
  getConversations(): Promise<Conversation[]> {
    return request('GET', '/conversations');
  },

  createConversation(title?: string, modelMode?: ModelMode): Promise<Conversation> {
    return request('POST', '/conversations', { title, modelMode });
  },

  deleteConversation(id: string): Promise<void> {
    return request('DELETE', `/conversations/${id}`);
  },

  getMessages(conversationId: string): Promise<Message[]> {
    return request('GET', `/conversations/${conversationId}/messages`);
  },

  // Chat — message is sent over WebSocket; this sends via REST as fallback
  sendMessage(params: {
    content: string;
    conversationId: string;
    modelMode: ModelMode;
    workspacePath?: string;
    history: { role: 'user' | 'assistant'; content: string }[];
  }): void {
    const sent = wsManager.send({
      type: 'send_message',
      ...params,
    });
    if (sent) wsManager.setActiveGeneration(params.conversationId);
    if (!sent) {
      // Don't leave the UI stuck on "Planning…" if the backend socket is down
      streamEvents.emit({
        type: 'error',
        conversationId: params.conversationId,
        error: 'Not connected to the backend. Check that uvicorn is running, then try again.',
      } as any);
    }
  },

  stopGeneration(): boolean {
    return wsManager.stopActiveGeneration();
  },

  finishGeneration(conversationId: string): void {
    wsManager.finishGeneration(conversationId);
  },

  // Workspace
  setWorkspace(path: string): Promise<WorkspaceInfo> {
    return request('POST', '/workspace', { path });
  },

  scanWorkspace(path: string): Promise<WorkspaceInfo> {
    return request('POST', '/workspace/scan', { path });
  },

  selectWorkspace(): Promise<{ cancelled: boolean; root?: string; files?: string[] }> {
    return request('GET', '/workspace/select');
  },

  // Status & Usage
  getStatus(): Promise<{ gemini: boolean; grok: boolean; ollama: boolean }> {
    return request('GET', '/status');
  },

  getUsageStats(): Promise<UsageStats> {
    return request('GET', '/usage');
  },

  // Settings
  saveSettings(settings: Record<string, unknown>): Promise<void> {
    return request('POST', '/settings', settings);
  },

  getAgentStatus(): Promise<AgentStatus> {
    return request('GET', '/agent/status');
  },
};
