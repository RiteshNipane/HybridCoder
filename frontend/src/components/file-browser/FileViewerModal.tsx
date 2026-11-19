import React, { useState } from 'react';
import { X, Save, Copy, Check, FileText } from 'lucide-react';

interface FileViewerModalProps {
  filePath: string;
  initialContent: string;
  onClose: () => void;
  onSave?: (newContent: string) => Promise<void>;
}

export const FileViewerModal: React.FC<FileViewerModalProps> = ({
  filePath,
  initialContent,
  onClose,
  onSave,
}) => {
  const [content, setContent] = useState(initialContent);
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedSuccess, setSavedSuccess] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleSave = async () => {
    if (!onSave) return;
    setSaving(true);
    try {
      await onSave(content);
      setSavedSuccess(true);
      setTimeout(() => setSavedSuccess(false), 2000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-md animate-fade-in">
      <div className="w-full max-w-4xl h-[80vh] flex flex-col rounded-2xl border border-gray-800 bg-[#0a0d14]/95 shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800/80 bg-gray-950/60">
          <div className="flex items-center gap-2 truncate">
            <FileText className="w-4 h-4 text-cyan-400 shrink-0" />
            <span className="font-mono text-xs font-medium text-gray-200 truncate">
              {filePath}
            </span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={handleCopy}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-gray-900 hover:bg-gray-800 border border-gray-800 text-xs text-gray-300 transition-colors"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copied ? 'Copied' : 'Copy'}</span>
            </button>
            {onSave && (
              <button
                onClick={handleSave}
                disabled={saving}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-cyan-600/20 hover:bg-cyan-600/30 border border-cyan-500/40 text-xs text-cyan-300 font-medium transition-colors"
              >
                {savedSuccess ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                    <span>Saved!</span>
                  </>
                ) : (
                  <>
                    <Save className="w-3.5 h-3.5" />
                    <span>{saving ? 'Saving…' : 'Save File'}</span>
                  </>
                )}
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Content Editor / Viewer */}
        <div className="flex-1 overflow-hidden relative">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="w-full h-full p-4 font-mono text-xs text-gray-200 bg-transparent resize-none focus:outline-none selection:bg-cyan-500/30 leading-relaxed"
            spellCheck={false}
          />
        </div>
      </div>
    </div>
  );
};
