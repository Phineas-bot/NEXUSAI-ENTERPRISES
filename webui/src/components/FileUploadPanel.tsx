import { useMemo, useState } from 'react';
import type { CloudConfig } from '../lib/api';
import { uploadRealFile } from '../lib/api';
import type { ClusterNode } from '../types';
import { selectPreferredNode } from '../lib/nodeSelectors';

type Props = {
  config: CloudConfig;
  nodes: ClusterNode[];
  onRefresh(): void;
};

export function FileUploadPanel({ config, nodes, onRefresh }: Props) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [sourceNode, setSourceNode] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [lastDatasetId, setLastDatasetId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const preferredNode = useMemo(() => selectPreferredNode(nodes), [nodes]);
  const entryNodeId = sourceNode || preferredNode?.id || '';

  const handleUpload = async () => {
    if (busy) return;
    if (!selectedFile) {
      setStatus('Choose a file to upload.');
      return;
    }
    if (!entryNodeId) {
      setStatus('No nodes are online to accept uploads.');
      return;
    }
    try {
      setBusy(true);
      setStatus('Uploading file into the fabric…');
      const response = await uploadRealFile(selectedFile, entryNodeId, config);
      setLastDatasetId(response.datasetId);
      setStatus(`Stored on ${response.targetNode}. Dataset id ${response.datasetId}.`);
      setSelectedFile(null);
      onRefresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setStatus(`Upload failed: ${message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/60 p-6 space-y-4">
      <header>
        <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Ingest</p>
        <h2 className="text-xl font-semibold text-white">Upload an actual file</h2>
        <p className="text-sm text-slate-400 mt-1">
          Files are persisted on disk then injected into the simulated storage network.
        </p>
      </header>

      <input
        type="file"
        onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
        className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-3 py-2 text-sm text-white file:mr-4 file:rounded-2xl file:border-0 file:bg-slate-800 file:px-4 file:py-2 file:text-xs file:uppercase file:tracking-[0.3em]"
      />

      <select
        value={sourceNode}
        onChange={(event) => setSourceNode(event.target.value)}
        className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-emerald-400 focus:outline-none"
      >
        <option value="">Auto-select entry node ({preferredNode?.id ?? 'none'})</option>
        {nodes.map((node) => (
          <option key={node.id} value={node.id}>
            {node.id}
          </option>
        ))}
      </select>

      <button
        onClick={handleUpload}
        disabled={busy}
        className="w-full rounded-2xl bg-gradient-to-r from-emerald-500 to-cyan-400 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-slate-950 disabled:opacity-50"
      >
        {busy ? 'Uploading…' : 'Upload file'}
      </button>

      <p className="text-xs text-slate-500">
        Entry node: {entryNodeId || 'none online'}
      </p>

      {lastDatasetId && <p className="text-xs text-emerald-400">Dataset id: {lastDatasetId}</p>}

      {status && <p className="text-sm text-slate-300">{status}</p>}
    </section>
  );
}
