import { useCallback } from 'react';
import { api } from '@/services/api';
import { useStore, selectActiveConversation } from '@/store';
import type { ModelMode } from '@/types';
import { nanoid } from './nanoid';

export function useChat() {
  const agentStatus = useStore((s) => s.agentStatus);
  const busy = agentStatus.phase !== 'idle' && agentStatus.phase !== 'error';

  const addMessage = useStore((s) => s.addMessage);
  const addConversation = useStore((s) => s.addConversation);
  const activeConversationId = useStore((s) => s.activeConversationId);
  const modelMode = useStore((s) => s.modelMode);
  const workspace = useStore((s) => s.workspace);
  const updateAgentStatus = useStore((s) => s.updateAgentStatus);
  const activeConversation = useStore(selectActiveConversation);

  const sendMessage = useCallback(
    async (content: string, overrideMode?: ModelMode) => {
      if (!content.trim()) return;

      let conversationId = activeConversationId;

      // Create a new conversation if none active
      if (!conversationId) {
        try {
          const conv = await api.createConversation();
          addConversation(conv);
          conversationId = conv.id;
        } catch {
          // Offline / backend not ready — create a local conversation
          const conv = {
            id: nanoid(),
            title: 'New Chat',
            createdAt: Date.now(),
            updatedAt: Date.now(),
            messages: [],
            modelMode,
          };
          addConversation(conv);
          conversationId = conv.id;
        }
      }

      // Snapshot the prior turns before adding this prompt. The model is stateless
      // between requests, so send the selected conversation's history each time.
      const history = (useStore.getState().conversations.find((c) => c.id === conversationId)?.messages ?? [])
        .filter((message) => (message.role === 'user' || message.role === 'assistant') && !message.streaming)
        .map(({ role, content }) => ({ role: role as 'user' | 'assistant', content }));

      // Add user message immediately
      addMessage(conversationId, {
        id: nanoid(),
        role: 'user',
        content,
        timestamp: Date.now(),
      });

      useStore.getState().clearModelTrail();
      updateAgentStatus({ phase: 'planning', label: 'Thinking…' });

      // Send over WebSocket (streaming response will come back via wsManager)
      api.sendMessage({
        content,
        conversationId,
        modelMode: overrideMode ?? modelMode,
        workspacePath: workspace?.path,
        history,
      });
    },
    [activeConversationId, modelMode, workspace, addMessage, addConversation, updateAgentStatus],
  );

  const stopGeneration = useCallback(() => {
    if (activeConversationId) {
      api.stopGeneration(activeConversationId);
    }
    updateAgentStatus({ phase: 'idle', label: 'Ready' });
  }, [activeConversationId, updateAgentStatus]);

  // Reflect streaming state from ws events
  const messages = activeConversation?.messages ?? [];
  const lastMsg = messages[messages.length - 1];
  const streaming = lastMsg?.streaming ?? false;

  return {
    sendMessage,
    stopGeneration,
    isStreaming: streaming || busy,
    conversationHistory: messages,
  };
}
