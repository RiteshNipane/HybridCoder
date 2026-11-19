import React from 'react';
import { Check, X } from 'lucide-react';
import { useStore } from '@/store';
import { PROVIDER_COLORS, PROVIDER_LABELS, getActiveModel } from '@/hooks/useActiveModel';

const shortModel = (m: string) => m.split('/').pop()?.replace(/-instruct$/, '') ?? m;

/**
 * Live "who is answering" chip: shows the model working right now, plus earlier attempts
 * in this request (e.g. NVIDIA ✗ → Gemini ● writing).
 */
export const ModelActivity: React.FC = () => {
  const trail = useStore((s) => s.modelTrail);
  if (trail.length === 0) return null;
  const active = getActiveModel(trail);

  return (
    <div className="flex items-center gap-2 text-[11px] font-mono">
      {trail.map((step, i) => {
        const color = PROVIDER_COLORS[step.provider] ?? '#9ca3af';
        const isActive = step === active;
        return (
          <React.Fragment key={`${step.provider}-${step.startedAt}-${i}`}>
            {i > 0 && <span className="text-gray-600">→</span>}
            <span
              title={step.error ? `${step.model} failed: ${step.error}` : step.model}
              className="flex items-center gap-1.5 px-2 py-0.5 rounded-full border"
              style={{
                borderColor: `${color}${isActive ? '90' : '40'}`,
                background: `${color}${isActive ? '22' : '10'}`,
                color: step.state === 'failed' ? '#f87171' : color,
                opacity: isActive || i === trail.length - 1 ? 1 : 0.7,
              }}
            >
              {step.state === 'failed' ? (
                <X className="w-3 h-3" />
              ) : step.state === 'done' ? (
                <Check className="w-3 h-3" />
              ) : (
                <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: color }} />
              )}
              <span>{PROVIDER_LABELS[step.provider] ?? step.provider}</span>
              <span className="opacity-60 hidden md:inline">{shortModel(step.model)}</span>
              {isActive && (
                <span className="opacity-80">{step.state === 'generating' ? 'writing…' : 'thinking…'}</span>
              )}
            </span>
          </React.Fragment>
        );
      })}
    </div>
  );
};
