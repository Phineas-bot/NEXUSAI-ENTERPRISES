import { useMemo, useState } from 'react';
import { FileEntry } from '../types';
import { formatBytes, formatRelative } from '../utils/formatters';

type Props = {
  recentFiles: FileEntry[];
  catalogFiles: FileEntry[];
};

export function FileExplorer({ recentFiles, catalogFiles }: Props) {
  const [view, setView] = useState<'recent' | 'catalog'>('recent');
  const files = view === 'recent' ? recentFiles : catalogFiles;
  const title = view === 'recent' ? 'Recent activity' : 'Cloud catalog';
  const subtitle = view === 'recent' ? 'Latest items touched in the last sync' : 'Complete dataset stored in the fabric';
  const emptyLabel = view === 'recent' ? 'No recent files yet.' : 'No files stored yet.';
  const summary = useMemo(() => {
    if (!files.length) {
      return emptyLabel;
    }
    if (view === 'recent') {
      return `${files.length} updated recently`;
    }
    return `${catalogFiles.length} total files`;
  }, [catalogFiles.length, emptyLabel, files.length, view]);
  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/40 p-6">
      <header className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Content</p>
          <h2 className="text-xl font-semibold text-white">{title}</h2>
          <p className="text-xs text-slate-500 mt-1">{subtitle}</p>
          <p className="text-xs text-slate-500">{summary}</p>
        </div>
        <div className="inline-flex rounded-full border border-white/10 text-xs uppercase tracking-[0.3em] text-slate-300">
          <button
            className={`px-4 py-2 rounded-full ${view === 'recent' ? 'bg-white/10 text-white' : 'text-slate-400'}`}
            onClick={() => setView('recent')}
          >
            Recent
          </button>
          <button
            className={`px-4 py-2 rounded-full ${view === 'catalog' ? 'bg-white/10 text-white' : 'text-slate-400'}`}
            onClick={() => setView('catalog')}
          >
            All files
          </button>
        </div>
      </header>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-slate-400">
              <th className="pb-3 font-medium">Name</th>
              <th className="pb-3 font-medium">Owner</th>
              <th className="pb-3 font-medium">Size</th>
              <th className="pb-3 font-medium">Updated</th>
              <th className="pb-3 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {files.length === 0 && (
              <tr>
                <td className="py-4 text-center text-slate-500" colSpan={5}>
                  {emptyLabel}
                </td>
              </tr>
            )}
            {files.map((file) => (
              <tr key={file.id} className="text-slate-100">
                <td className="py-3 font-medium">
                  {file.name}
                  {file.isFolder ? <span className="ml-2 text-[10px] uppercase text-slate-500">Folder</span> : null}
                </td>
                <td className="py-3 text-slate-400">{file.owner}</td>
                <td className="py-3 text-slate-400">{formatBytes(file.sizeBytes)}</td>
                <td className="py-3 text-slate-400">{formatRelative(file.updatedAt)}</td>
                <td className="py-3 text-right">
                  <button className="px-3 py-1 text-xs rounded-full border border-white/10 text-white hover:bg-white/10">
                    View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
