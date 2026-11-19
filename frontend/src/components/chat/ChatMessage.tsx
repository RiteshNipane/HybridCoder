import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import {
  Copy,
  Check,
  Terminal,
  FileCode,
  CheckCircle2,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Wrench,
  FilePlus,
  FolderPlus,
  FolderOpen,
  Search,
} from 'lucide-react';
import type { Message, ToolExecution } from '@/types';

interface ChatMessageProps {
  message: Message;
}

const ToolCard: React.FC<{ tool: ToolExecution }> = ({ tool }) => {
  const [expanded, setExpanded] = useState(false);
  const isPending = tool.output === undefined;
  const isError = tool.error === true;

  const getToolIcon = (name: string) => {
    switch (name) {
      case 'create_file':
      case 'write_file':
        return <FilePlus className="w-3.5 h-3.5 text-cyan-400" />;
      case 'create_directory':
        return <FolderPlus className="w-3.5 h-3.5 text-cyan-400" />;
      case 'edit_file':
        return <FileCode className="w-3.5 h-3.5 text-emerald-400" />;
      case 'read_file':
        return <FolderOpen className="w-3.5 h-3.5 text-blue-400" />;
      case 'run_command':
        return <Terminal className="w-3.5 h-3.5 text-amber-400" />;
      case 'search_files':
        return <Search className="w-3.5 h-3.5 text-purple-400" />;
      default:
        return <Wrench className="w-3.5 h-3.5 text-gray-400" />;
    }
  };

  const getTargetArg = () => {
    if (tool.args?.path) return tool.args.path;
    if (tool.args?.command) return tool.args.command;
    if (tool.args?.pattern) return `pattern: ${tool.args.pattern}`;
    return '';
  };

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/70 overflow-hidden text-xs">
      <div
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-white/[0.03] transition-colors select-none"
      >
        <div className="flex items-center gap-2 truncate">
          {getToolIcon(tool.name)}
          <span className="font-mono font-medium text-gray-200">{tool.name}</span>
          {getTargetArg() && (
            <span className="font-mono text-gray-400 truncate max-w-[280px]">
              {getTargetArg()}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {isPending ? (
            <span className="flex items-center gap-1.5 text-cyan-400 text-[11px]">
              <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
              Running…
            </span>
          ) : isError ? (
            <span className="flex items-center gap-1 text-red-400 text-[11px]">
              <AlertCircle className="w-3 h-3" />
              Failed
            </span>
          ) : (
            <span className="flex items-center gap-1 text-emerald-400 text-[11px]">
              <CheckCircle2 className="w-3 h-3" />
              Done
            </span>
          )}
          {expanded ? (
            <ChevronDown className="w-3.5 h-3.5 text-gray-500" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-gray-500" />
          )}
        </div>
      </div>

      {expanded && (
        <div className="p-3 bg-black/60 border-t border-gray-800/80 font-mono text-[11px] space-y-2">
          {tool.args && Object.keys(tool.args).length > 0 && (
            <div>
              <div className="text-[10px] uppercase text-gray-500 font-semibold mb-1">
                Arguments
              </div>
              <pre className="p-2 rounded bg-gray-900/80 text-gray-300 overflow-x-auto max-h-40">
                {JSON.stringify(tool.args, null, 2)}
              </pre>
            </div>
          )}
          {tool.output && (
            <div>
              <div className="text-[10px] uppercase text-gray-500 font-semibold mb-1">
                Output
              </div>
              <pre
                className={`p-2 rounded overflow-x-auto max-h-60 ${
                  isError ? 'bg-red-950/40 text-red-300' : 'bg-gray-900/80 text-gray-200'
                }`}
              >
                {tool.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const isUser = message.role === 'user';
  const [copiedCode, setCopiedCode] = useState<string | null>(null);
  const [copiedMessage, setCopiedMessage] = useState(false);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedCode(text);
    setTimeout(() => setCopiedCode(null), 2000);
  };

  const handleCopyMessage = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedMessage(true);
      setTimeout(() => setCopiedMessage(false), 1800);
    } catch {
      setCopiedMessage(false);
    }
  };

  const getModelBadge = () => {
    if (isUser) return null;
    const model = (message.model || message.modelMode || 'ASSISTANT').toUpperCase();
    let badgeColor = 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30';
    if (model.includes('GEMINI')) {
      badgeColor = 'bg-blue-500/20 text-blue-300 border-blue-500/30';
    } else if (model.includes('GROK')) {
      badgeColor = 'bg-purple-500/20 text-purple-300 border-purple-500/30';
    } else if (model.includes('QWEN') || model.includes('OLLAMA') || model.includes('LOCAL')) {
      badgeColor = 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30';
    }

    return (
      <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full border ${badgeColor}`}>
        {model}
      </span>
    );
  };

  return (
    <div
      className={`flex flex-col my-3 animate-fade-in ${
        isUser ? 'ml-auto items-end max-w-[85%]' : 'mr-auto items-start w-full'
      }`}
    >
      <div className="flex items-center justify-between gap-3 w-full mb-1 px-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-medium text-gray-400">
            {isUser ? 'You' : 'Agent'}
          </span>
          {getModelBadge()}
          <span className="text-[10px] text-gray-500 shrink-0">
            {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>
        <button
          type="button"
          onClick={handleCopyMessage}
          disabled={!isUser && message.streaming}
          title={isUser ? 'Copy prompt' : 'Copy answer'}
          aria-label={isUser ? 'Copy prompt' : 'Copy answer'}
          className="flex items-center gap-1 px-1.5 py-1 rounded text-[10px] text-gray-500 hover:text-cyan-300 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {copiedMessage ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
          <span>{copiedMessage ? 'Copied' : isUser ? 'Copy prompt' : 'Copy answer'}</span>
        </button>
      </div>

      <div
        className={`p-4 rounded-2xl backdrop-blur-md border text-sm leading-relaxed transition-all shadow-lg ${
          isUser
            ? 'bg-gradient-to-br from-cyan-400/15 to-cyan-600/10 border-cyan-400/25 text-cyan-50 rounded-tr-sm'
            : 'bg-[#0d1117]/75 border-white/10 text-gray-100 rounded-tl-sm w-full'
        }`}
      >
        {/* Tool executions (Claude Code style) */}
        {message.toolExecutions && message.toolExecutions.length > 0 && (
          <div className="mb-3 space-y-1.5">
            {message.toolExecutions.map((tool) => (
              <ToolCard key={tool.id} tool={tool} />
            ))}
          </div>
        )}

        {/* File changes summary if present */}
        {message.fileChanges && message.fileChanges.length > 0 && (
          <div className="mb-3 p-2.5 rounded-lg bg-black/40 border border-gray-800">
            <div className="flex items-center gap-2 text-xs font-medium text-emerald-400 mb-1.5">
              <FileCode className="w-3.5 h-3.5" />
              <span>Modified Files ({message.fileChanges.length})</span>
            </div>
            <ul className="space-y-1">
              {message.fileChanges.map((fc, idx) => (
                <li key={idx} className="text-xs font-mono text-gray-300 flex items-center gap-2">
                  <span className="text-emerald-500">✓</span> {fc.path}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Message body markdown */}
        <div className="prose prose-invert prose-sm max-w-none break-words">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
            components={{
              code({ node, inline, className, children, ...props }: any) {
                const match = /language-(\w+)/.exec(className || '');
                const codeString = String(children).replace(/\n$/, '');
                if (!inline && match) {
                  return (
                    <div className="relative group my-3 rounded-lg overflow-hidden border border-gray-800 bg-gray-950/80">
                      <div className="flex items-center justify-between px-3 py-1 bg-gray-900/80 border-b border-gray-800 text-[11px] text-gray-400 font-mono">
                        <span>{match[1]}</span>
                        <button
                          onClick={() => handleCopy(codeString)}
                          className="flex items-center gap-1 hover:text-cyan-400 transition-colors"
                        >
                          {copiedCode === codeString ? (
                            <>
                              <Check className="w-3 h-3 text-emerald-400" />
                              <span className="text-emerald-400">Copied</span>
                            </>
                          ) : (
                            <>
                              <Copy className="w-3 h-3" />
                              <span>Copy</span>
                            </>
                          )}
                        </button>
                      </div>
                      <pre className="p-3 overflow-x-auto text-xs font-mono">
                        <code className={className} {...props}>
                          {children}
                        </code>
                      </pre>
                    </div>
                  );
                }
                return (
                  <code className="bg-gray-800/60 px-1 py-0.5 rounded text-xs font-mono text-cyan-300" {...props}>
                    {children}
                  </code>
                );
              },
            }}
          >
            {message.content}
          </ReactMarkdown>

          {/* Streaming cursor */}
          {message.streaming && (
            <span className="inline-block w-2 h-4 ml-1 bg-cyan-400 animate-pulse align-middle" />
          )}
        </div>
      </div>
    </div>
  );
};

