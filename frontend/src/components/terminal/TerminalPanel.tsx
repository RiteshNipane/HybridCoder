import React, { useState } from 'react';
import { Terminal, X, Play, Trash2, CheckCircle2, AlertCircle } from 'lucide-react';

interface CommandOutput {
  command: string;
  stdout: string;
  stderr: string;
  returncode: number;
  timed_out: boolean;
  timestamp: number;
}

export const TerminalPanel: React.FC<{ isOpen: boolean; onClose: () => void }> = ({
  isOpen,
  onClose,
}) => {
  const [command, setCommand] = useState('');
  const [outputs, setOutputs] = useState<CommandOutput[]>([]);
  const [running, setRunning] = useState(false);

  if (!isOpen) return null;

  const handleRun = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!command.trim() || running) return;

    const cmdToRun = command.trim();
    setRunning(true);
    try {
      const res = await fetch('http://localhost:8000/api/terminal/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmdToRun, timeout: 60 }),
      });
      const data = await res.json();
      setOutputs((prev) => [
        ...prev,
        {
          command: cmdToRun,
          stdout: data.stdout || '',
          stderr: data.stderr || '',
          returncode: data.returncode ?? 0,
          timed_out: !!data.timed_out,
          timestamp: Date.now(),
        },
      ]);
      setCommand('');
    } catch (err: any) {
      setOutputs((prev) => [
        ...prev,
        {
          command: cmdToRun,
          stdout: '',
          stderr: err.message || 'Execution failed',
          returncode: -1,
          timed_out: false,
          timestamp: Date.now(),
        },
      ]);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 h-72 border-t border-gray-800 bg-[#080b11]/95 backdrop-blur-xl flex flex-col shadow-2xl animate-slide-up">
      {/* Terminal Title Bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800 bg-gray-950/80 select-none">
        <div className="flex items-center gap-2 text-xs font-medium text-gray-300">
          <Terminal className="w-3.5 h-3.5 text-cyan-400" />
          <span>Workspace Terminal (Claude Code runner)</span>
        </div>
        <div className="flex items-center gap-2">
          {outputs.length > 0 && (
            <button
              onClick={() => setOutputs([])}
              className="p-1 hover:bg-gray-800 rounded text-gray-400 hover:text-gray-200 text-xs transition-colors"
              title="Clear terminal"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
          <button
            onClick={onClose}
            className="p-1 hover:bg-gray-800 rounded text-gray-400 hover:text-gray-200 transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Output Console */}
      <div className="flex-1 overflow-y-auto p-3 font-mono text-[11px] space-y-3 leading-relaxed">
        {outputs.length === 0 ? (
          <div className="text-gray-600 italic">
            Execute commands directly in your workspace. (e.g. `python -m pytest`, `npm run build`, `git status`)
          </div>
        ) : (
          outputs.map((out, idx) => (
            <div key={idx} className="space-y-1">
              <div className="flex items-center gap-2 text-cyan-400">
                <span className="text-gray-500">$</span>
                <span className="font-semibold text-gray-200">{out.command}</span>
                <span className="text-[10px] text-gray-500 ml-auto">
                  {new Date(out.timestamp).toLocaleTimeString()}
                </span>
                {out.returncode === 0 ? (
                  <CheckCircle2 className="w-3 h-3 text-emerald-400 shrink-0" />
                ) : (
                  <AlertCircle className="w-3 h-3 text-red-400 shrink-0" />
                )}
              </div>
              {out.stdout && (
                <pre className="p-2 rounded bg-black/60 text-gray-300 overflow-x-auto whitespace-pre-wrap">
                  {out.stdout}
                </pre>
              )}
              {out.stderr && (
                <pre className="p-2 rounded bg-red-950/40 text-red-300 border border-red-900/40 overflow-x-auto whitespace-pre-wrap">
                  {out.stderr}
                </pre>
              )}
            </div>
          ))
        )}
      </div>

      {/* Input Bar */}
      <form onSubmit={handleRun} className="flex items-center gap-2 p-2 border-t border-gray-800 bg-gray-950/60">
        <span className="text-cyan-400 font-mono text-xs pl-2">$</span>
        <input
          type="text"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          placeholder="Enter command to run in workspace…"
          disabled={running}
          className="flex-1 px-2 py-1 bg-transparent font-mono text-xs text-gray-200 focus:outline-none placeholder-gray-600"
        />
        <button
          type="submit"
          disabled={running || !command.trim()}
          className="flex items-center gap-1.5 px-3 py-1 rounded bg-cyan-600/30 hover:bg-cyan-600/50 border border-cyan-500/40 text-cyan-300 text-xs font-medium disabled:opacity-40 transition-all"
        >
          <Play className="w-3 h-3 fill-current" />
          <span>{running ? 'Running…' : 'Run'}</span>
        </button>
      </form>
    </div>
  );
};
