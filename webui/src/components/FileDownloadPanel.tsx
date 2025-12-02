import { useState } from 'react';
import type { CloudConfig } from '../lib/api';
import { downloadRealFile } from '../lib/api';

type Props = {
  config: CloudConfig;
};

export function FileDownloadPanel({ config }: Props) {
  const [datasetId, setDatasetId] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleDownload = async () => {
    if (busy) return;
    if (!datasetId.trim()) {
      setStatus('Enter the dataset id returned when the file was uploaded.');
      return;
    }
    try {
      setBusy(true);
      setStatus('Preparing download…');
      const blob = await downloadRealFile(datasetId.trim(), config);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${datasetId.trim()}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setStatus('Download started.');
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setStatus(`Download failed: ${message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/60 p-6 space-y-4">
      <header>
        <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Egress</p>
        <h2 className="text-xl font-semibold text-white">Download actual data</h2>
        <p className="text-sm text-slate-400 mt-1">
          Paste the dataset id you received after uploading to retrieve the stored bytes.
        </p>
      </header>

      <input
        value={datasetId}
        onChange={(event) => setDatasetId(event.target.value)}
        placeholder="dataset id"
        className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
      />
      <button
        onClick={handleDownload}
        disabled={busy}
        className="w-full rounded-2xl border border-white/20 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-white disabled:opacity-40"
      >
        {busy ? 'Fetching…' : 'Download file'}
      </button>

      {status && <p className="text-sm text-slate-300">{status}</p>}
    </section>
  );
}
