import { FileEntry } from '../types';
import { formatBytes, formatRelative } from '../utils/formatters';

type Props = {
  files: FileEntry[];
};

export function FileExplorer({ files }: Props) {
  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/40 p-6">
      <header className="flex items-center justify-between mb-6">
        <div>
          <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Content</p>
          <h2 className="text-xl font-semibold text-white">Recent activity</h2>
        </div>
        <button className="px-4 py-2 rounded-full border border-sky-400/40 text-sm text-sky-200 hover:bg-sky-500/10">
          New upload
        </button>
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
            {files.map((file) => (
              <tr key={file.id} className="text-slate-100">
                <td className="py-3 font-medium">{file.name}</td>
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
