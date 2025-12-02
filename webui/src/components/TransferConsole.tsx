import { useState } from 'react';
import type { CloudConfig, FileFetchPayload, FilePushPayload, FileTransferPayload } from '../lib/api';
import { fetchFile, initiateTransfer, pushFile } from '../lib/api';
import type { ClusterNode } from '../types';

type Props = {
  nodes: ClusterNode[];
  config: CloudConfig;
  onRefresh(): void;
};

export function TransferConsole({ nodes, config, onRefresh }: Props) {
  const [sourceNode, setSourceNode] = useState('');
  const [targetNode, setTargetNode] = useState('');
  const [fileName, setFileName] = useState('demo.bin');
  const [sizeMb, setSizeMb] = useState(50);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const withStatus = async (label: string, fn: () => Promise<void>) => {
    try {
      setBusy(true);
      setStatus(`${label} running...`);
      await fn();
      setStatus(`${label} complete`);
      onRefresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setStatus(`${label} failed: ${message}`);
    } finally {
      setBusy(false);
    }
  };

  const buildPushPayload = (): FilePushPayload => ({ sourceNode, fileName, sizeMb });
  const buildTransferPayload = (): FileTransferPayload => ({ sourceNode, targetNode, fileName, sizeMb });
  const buildFetchPayload = (): FileFetchPayload => ({ targetNode, fileName });

  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/60 p-6 space-y-4">
      <header>
        <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Pipelines</p>
        <h2 className="text-xl font-semibold text-white">Transfer console</h2>
        <p className="text-sm text-slate-400 mt-1">Kick off manual push/fetch operations to showcase replication and routing.</p>
      </header>

      <select
        value={sourceNode}
        onChange={(e) => setSourceNode(e.target.value)}
        className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
      >
        <option value="">Source node…</option>
        {nodes.map((node) => (
          <option key={node.id} value={node.id}>
            {node.id}
          </option>
        ))}
      </select>

      <select
        value={targetNode}
        onChange={(e) => setTargetNode(e.target.value)}
        className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
      >
        <option value="">Target node…</option>
        {nodes.map((node) => (
          <option key={node.id} value={node.id}>
            {node.id}
          </option>
        ))}
      </select>

      <input
        value={fileName}
        onChange={(e) => setFileName(e.target.value)}
        placeholder="file.bin"
        className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
      />

      <input
        type="number"
        min={1}
        max={1024}
        value={sizeMb}
        onChange={(e) => setSizeMb(Number(e.target.value))}
        className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
      />

      <div className="grid grid-cols-3 gap-3">
        <button
          onClick={() => withStatus('Push file', () => pushFile(buildPushPayload(), config).then(() => undefined))}
          disabled={!sourceNode || busy}
          className="rounded-2xl bg-gradient-to-r from-amber-500 to-rose-400 py-2 text-xs font-semibold uppercase tracking-[0.3em] text-slate-950 disabled:opacity-40"
        >
          Push
        </button>
        <button
          onClick={() => withStatus('Manual transfer', () => initiateTransfer(buildTransferPayload(), config).then(() => undefined))}
          disabled={!sourceNode || !targetNode || busy}
          className="rounded-2xl bg-gradient-to-r from-emerald-500 to-sky-400 py-2 text-xs font-semibold uppercase tracking-[0.3em] text-slate-950 disabled:opacity-40"
        >
          Transfer
        </button>
        <button
          onClick={() => withStatus('Fetch file', () => fetchFile(buildFetchPayload(), config).then(() => undefined))}
          disabled={!targetNode || busy}
          className="rounded-2xl border border-white/20 py-2 text-xs font-semibold uppercase tracking-[0.3em] text-white disabled:opacity-40"
        >
          Fetch
        </button>
      </div>

      {status && <p className="text-sm text-slate-300">{status}</p>}
    </section>
  );
}
