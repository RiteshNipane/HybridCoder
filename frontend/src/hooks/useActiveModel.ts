import { useStore } from '@/store';
import type { ModelStep } from '@/types';

/** Friendly names for providers (the 'grok' slot is actually NVIDIA NIM). */
export const PROVIDER_LABELS: Record<string, string> = {
  nvidia: 'NVIDIA',
  grok: 'NVIDIA',
  gemini: 'Gemini',
  ollama: 'Local Qwen',
};

export const PROVIDER_COLORS: Record<string, string> = {
  nvidia: '#76b900',
  grok: '#76b900',
  gemini: '#60a5fa',
  ollama: '#34d399',
};

/** Pure function: which model is working on the answer right now? (null when none) */
export function getActiveModel(trail: ModelStep[]): ModelStep | null {
  for (let i = trail.length - 1; i >= 0; i--) {
    const step = trail[i];
    if (step.state === 'thinking' || step.state === 'generating') return step;
  }
  return null;
}

/** React hook: the model currently working on the answer, updated live over the WebSocket. */
export function useActiveModel(): ModelStep | null {
  const trail = useStore((s) => s.modelTrail);
  return getActiveModel(trail);
}
