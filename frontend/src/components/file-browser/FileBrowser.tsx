import React, { useEffect, useState } from 'react';
import {
  Folder,
  FileCode,
  FilePlus,
  RefreshCw,
  Search,
  Trash2,
  AlertCircle,
} from 'lucide-react';
import { FileViewerModal } from './FileViewerModal';

interface FileEntry {
  path: string;
  size: number;
  is_dir: boolean;
}

export const FileBrowser: React.FC = () => {
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [rootPath, setRootPath] = useState<string>('');
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Selected file for viewer modal
  const [activeFile, setActiveFile] = useState<{ path: string; content: string } | null>(null);
  const [creatingFile, setCreatingFile] = useState(false);
  const [newFilePath, setNewFilePath] = useState('');

  const fetchFiles = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('http://localhost:8000/api/files/tree');
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setRootPath(data.root || '');
      setFiles(data.files || []);
    } catch (e: any) {
      setError(e.message || 'Failed loading file tree');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchFiles();
  }, []);

  const handleOpenFile = async (relPath: string) => {
    try {
      const res = await fetch(`http://localhost:8000/api/files/read?path=${encodeURIComponent(relPath)}`);
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setActiveFile({ path: relPath, content: data.content });
    } catch (e: any) {
      alert(`Could not open file: ${e.message}`);
    }
  };

  const handleSaveFile = async (newContent: string) => {
    if (!activeFile) return;
    const res = await fetch('http://localhost:8000/api/files/write', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: activeFile.path, content: newContent }),
    });
    if (!res.ok) throw new Error(await res.text());
    await fetchFiles();
  };

  const handleCreateNewFile = async () => {
    if (!newFilePath.trim()) return;
    try {
      const res = await fetch('http://localhost:8000/api/files/write', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: newFilePath.trim(), content: '' }),
      });
      if (!res.ok) throw new Error(await res.text());
      setCreatingFile(false);
      setNewFilePath('');
      await fetchFiles();
    } catch (e: any) {
      alert(`Failed creating file: ${e.message}`);
    }
  };

  const handleDeleteFile = async (relPath: string) => {
    if (!confirm(`Are you sure you want to delete ${relPath}?`)) return;
    try {
      const res = await fetch('http://localhost:8000/api/files/delete', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: relPath }),
      });
      if (!res.ok) throw new Error(await res.text());
      await fetchFiles();
    } catch (e: any) {
      alert(`Failed deleting file: ${e.message}`);
    }
  };

  const filteredFiles = files.filter((f) =>
    f.path.toLowerCase().includes(searchQuery.toLowerCase()),
  );

  return (
    <div className="flex flex-col h-full overflow-hidden text-xs">
      {/* Workspace header & Actions */}
      <div className="flex items-center justify-between pb-2 mb-2 border-b border-gray-800/80">
        <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
          Workspace Files
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setCreatingFile(true)}
            className="p-1 rounded hover:bg-gray-800 text-gray-400 hover:text-cyan-300 transition-colors"
            title="Create new file"
          >
            <FilePlus className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={fetchFiles}
            disabled={loading}
            className="p-1 rounded hover:bg-gray-800 text-gray-400 hover:text-cyan-300 transition-colors"
            title="Refresh files"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-cyan-400' : ''}`} />
          </button>
        </div>
      </div>

      {/* New file input form */}
      {creatingFile && (
        <div className="p-2 mb-2 rounded bg-gray-900 border border-cyan-500/40 space-y-1.5">
          <input
            type="text"
            placeholder="path/to/new_file.py"
            value={newFilePath}
            onChange={(e) => setNewFilePath(e.target.value)}
            className="w-full px-2 py-1 bg-black/60 border border-gray-700 rounded text-xs font-mono text-gray-200 focus:outline-none focus:border-cyan-500"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreateNewFile();
              if (e.key === 'Escape') setCreatingFile(false);
            }}
          />
          <div className="flex items-center justify-end gap-1.5">
            <button
              onClick={() => setCreatingFile(false)}
              className="px-2 py-0.5 rounded text-[10px] text-gray-400 hover:text-gray-200"
            >
              Cancel
            </button>
            <button
              onClick={handleCreateNewFile}
              className="px-2 py-0.5 rounded bg-cyan-600/30 hover:bg-cyan-600/40 text-cyan-300 text-[10px] font-medium"
            >
              Create
            </button>
          </div>
        </div>
      )}

      {/* Search Input */}
      <div className="relative mb-2">
        <Search className="w-3.5 h-3.5 absolute left-2 top-2 text-gray-500" />
        <input
          type="text"
          placeholder="Filter files…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full pl-7 pr-2 py-1 rounded bg-black/40 border border-gray-800 text-xs text-gray-300 focus:outline-none focus:border-cyan-500/50"
        />
      </div>

      {/* File List */}
      <div className="flex-1 overflow-y-auto space-y-0.5 pr-1 font-mono text-[11px]">
        {error && (
          <div className="flex items-center gap-1.5 p-2 rounded bg-red-950/40 border border-red-900/50 text-red-300 text-xs">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span className="truncate">{error}</span>
          </div>
        )}

        {filteredFiles.length === 0 && !loading && (
          <div className="px-2 py-4 text-center text-gray-500 text-xs">
            {searchQuery ? 'No matching files' : 'No files in workspace'}
          </div>
        )}

        {filteredFiles.map((f) => (
          <div
            key={f.path}
            onClick={() => !f.is_dir && handleOpenFile(f.path)}
            className={`group flex items-center justify-between px-2 py-1.5 rounded transition-all select-none ${
              f.is_dir
                ? 'text-gray-400 font-semibold cursor-default'
                : 'text-gray-300 hover:bg-gray-800/60 hover:text-cyan-300 cursor-pointer'
            }`}
          >
            <div className="flex items-center gap-1.5 truncate">
              {f.is_dir ? (
                <Folder className="w-3.5 h-3.5 text-cyan-500/70 shrink-0" />
              ) : (
                <FileCode className="w-3.5 h-3.5 text-gray-500 group-hover:text-cyan-400 shrink-0" />
              )}
              <span className="truncate">{f.path}</span>
            </div>

            {!f.is_dir && (
              <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDeleteFile(f.path);
                  }}
                  className="p-1 hover:text-red-400 transition-colors"
                  title="Delete file"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Active File Modal */}
      {activeFile && (
        <FileViewerModal
          filePath={activeFile.path}
          initialContent={activeFile.content}
          onClose={() => setActiveFile(null)}
          onSave={handleSaveFile}
        />
      )}
    </div>
  );
};
