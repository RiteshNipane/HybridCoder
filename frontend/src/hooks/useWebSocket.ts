import { useEffect, useRef, useState } from 'react';
import { api, wsManager, streamEvents } from '@/services/api';
import { useStore } from '@/store';
import { nanoid } from './nanoid';
import type { ToolExecution } from '@/types';

const WS_URL =
  typeof window !== 'undefined'
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`
    : 'ws://localhost:8000/ws';

export function useWebSocket() {
  const [connected, setConnected] = useState(false);
  const setWsConnected = useStore((s) => s.setWsConnected);
  const addMessage = useStore((s) => s.addMessage);
  const appendToLastMessage = useStore((s) => s.appendToLastMessage);
  const updateLastMessage = useStore((s) => s.updateLastMessage);
  const updateAgentStatus = useStore((s) => s.updateAgentStatus);
  const activeConversationId = useStore((s) => s.activeConversationId);

  // Track active streaming message per conversation
  const streamingRef = useRef<Map<string, string>>(new Map());
  const lastErrorRef = useRef<{ msg: string; t: number }>({ msg: '', t: 0 });

  useEffect(() => {
    wsManager.connect(WS_URL);

    const unsubConn = wsManager.onConnectionChange((c) => {
      setConnected(c);
      setWsConnected(c);
      if (!c) {
        // Connection dropped mid-request: stop the "Planning…" spinner
        const ids = Array.from(streamingRef.current.keys());
        streamingRef.current.clear();
        ids.forEach((id) => updateLastMessage(id, { streaming: false }));
        updateAgentStatus({ phase: 'idle', label: 'Ready' });
      }
    });

    const unsubEvents = streamEvents.on((event: any) => {
      const convId =
        event.conversationId || useStore.getState().activeConversationId || 'default';
      const type = event.type;

      // 0. Live "which model is working right now" events
      if (type === 'model_activity') {
        useStore.getState().setModelActivity({
          provider: event.provider,
          model: event.model,
          state: event.state,
          error: event.error,
        });
        return;
      }

      // 1. Text chunk
      if (type === 'text' || type === 'chunk') {
        const text = event.content || event.text || event.payload?.text || '';
        const model = event.model || event.payload?.model || 'assistant';

        if (!streamingRef.current.has(convId)) {
          const msgId = nanoid();
          streamingRef.current.set(convId, msgId);
          addMessage(convId, {
            id: msgId,
            role: 'assistant',
            content: text,
            model,
            timestamp: Date.now(),
            streaming: true,
          });
        } else {
          appendToLastMessage(convId, text);
        }
      }

      // 2. Status update
      else if (type === 'status' || type === 'agent_status') {
        const message = event.message || event.payload?.label || 'Processing...';
        updateAgentStatus({
          phase: 'coding',
          label: message,
          currentTask: message,
        });
      }

      // 2.5 Tool call & execution (Mini Claude Code)
      else if (type === 'tool_call') {
        const toolExecution: ToolExecution = {
          id: nanoid(),
          name: event.name || 'tool',
          args: event.args || {},
          timestamp: Date.now(),
        };
        const currentConv = useStore.getState().conversations.find((c) => c.id === convId);
        const lastMsg = currentConv?.messages[currentConv.messages.length - 1];
        if (lastMsg && lastMsg.role === 'assistant') {
          const list = [...(lastMsg.toolExecutions || []), toolExecution];
          updateLastMessage(convId, { toolExecutions: list });
        } else {
          const msgId = nanoid();
          streamingRef.current.set(convId, msgId);
          addMessage(convId, {
            id: msgId,
            role: 'assistant',
            content: '',
            model: event.model || 'assistant',
            timestamp: Date.now(),
            streaming: true,
            toolExecutions: [toolExecution],
          });
        }
      }

      // 2.6 Tool result
      else if (type === 'tool_result') {
        const currentConv = useStore.getState().conversations.find((c) => c.id === convId);
        const lastMsg = currentConv?.messages[currentConv.messages.length - 1];
        if (lastMsg && lastMsg.toolExecutions && lastMsg.toolExecutions.length > 0) {
          const list = lastMsg.toolExecutions.map((t) => ({ ...t }));
          const target = [...list].reverse().find((t) => t.name === event.name && t.output === undefined);
          if (target) {
            target.output = event.output || '(completed)';
            target.error = event.error;
          } else {
            list[list.length - 1].output = event.output || '(completed)';
            list[list.length - 1].error = event.error;
          }
          updateLastMessage(convId, { toolExecutions: list });
        }
      }

      // 3. File modifications
      else if (type === 'edit' || type === 'file_change') {
        const files: string[] = event.files || event.payload || [];
        const action = event.action === 'create' ? 'create' : event.action === 'delete' ? 'delete' : 'edit';
        updateLastMessage(convId, {
          fileChanges: files.map((f: string) => ({ path: f, action })),
        });
      }

      // 4. Test run results
      else if (type === 'test') {
        const results = event.results || {};
        const runner = results.runner_used || 'auto';
        let summary: string;
        if (runner === 'patch-validation') {
          summary = `\n\n**Patch validation failed**: ${results.output || 'The proposed edit could not be applied.'}`;
        } else if (results.skipped) {
          summary = `\n\n**Verification skipped**: ${results.output || 'No matching test or compiler check was available.'}`;
        } else if (results.counts_known === false) {
          const reason = results.timed_out
            ? 'timed out'
            : `exited with code ${results.returncode ?? 'unknown'}`;
          summary = `\n\n**Verification ${results.check_passed ? 'passed' : 'failed'}**: ${runner} ${reason}; test counts were unavailable.`;
        } else {
          const status = results.check_passed === false ? 'failed' : 'passed';
          summary = `\n\n**Verification ${status}**: ${results.passed ?? 0} passed, ${results.failed ?? 0} failed (${runner})`;
        }
        if (results.command) {
          const command = String(results.command).replace(/`/g, '\\`');
          summary += `\n\nCommand: \`${command}\``;
        }
        if (results.workspace) {
          summary += `\n\nWorkspace: \`${String(results.workspace).replace(/`/g, '\\`')}\``;
        }
        const output = String(results.output || '').trim();
        if (!results.skipped && results.check_passed === false && output) {
          const excerpt = output.slice(-3000).replace(/```/g, '` ` `');
          summary += `\n\nFailure output:\n\n\`\`\`text\n${excerpt}\n\`\`\``;
        }
        appendToLastMessage(convId, summary);
      }

      // Patch application and syntax validation are not test results.
      else if (type === 'validation') {
        const results = event.results || {};
        const output = String(results.output || 'The proposed patch could not be applied.').trim();
        appendToLastMessage(convId, `\n\n**Patch validation failed**: ${output}`);
      }

      // 5. Completion
      else if (type === 'done') {
        const model = event.model || 'hybrid';
        streamingRef.current.delete(convId);
        api.finishGeneration(convId);
        updateLastMessage(convId, {
          streaming: false,
          model,
        });
        updateAgentStatus({ phase: 'idle', label: 'Ready' });
      }

      // 6. Error
      else if (type === 'error') {
        const errorMsg = event.error || event.message || 'Unknown error';
        streamingRef.current.delete(convId);
        api.finishGeneration(convId);
        updateAgentStatus({ phase: 'error', label: 'Error' });

        // The backend sends each error twice (with and without conversationId): show it once.
        const now = Date.now();
        if (lastErrorRef.current.msg === errorMsg && now - lastErrorRef.current.t < 2000) return;
        lastErrorRef.current = { msg: errorMsg, t: now };

        // Stop any half-streamed message, then show the error IN the chat.
        updateLastMessage(convId, { streaming: false });
        addMessage(convId, {
          id: nanoid(),
          role: 'assistant',
          content: `⚠️ **Request failed**\n\n${errorMsg}`,
          model: 'system',
          timestamp: now,
        });
      }

      // 7. Explicit stop acknowledgment
      else if (type === 'cancelled') {
        streamingRef.current.delete(convId);
        api.finishGeneration(convId);
        updateLastMessage(convId, { streaming: false });
        updateAgentStatus({ phase: 'idle', label: event.was_running ? 'Stopped' : 'Ready' });
      }

      // 8. Another request arrived while the active one was still running.
      else if (type === 'busy') {
        updateAgentStatus({ phase: 'coding', label: event.message || 'A request is already running.' });
      }
    });

    return () => {
      unsubConn();
      unsubEvents();
      wsManager.disconnect();
    };
    // Connect ONCE. (It used to depend on activeConversationId, which tore the
    // socket down right after the first message was sent.)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { connected, activeConversationId };
}
