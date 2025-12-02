import { useEffect, useState } from 'react';
import type { CloudConfig } from '../lib/api';
import { inspectNode } from '../lib/api';
import type { NodeDetail } from '../types';

type Props = {
  nodeId: string | null;
  config: CloudConfig;
  onClose(): void;
};

export function NodeInspector({ nodeId, config, onClose }: Props) {
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!nodeId) return;
    setLoading(true);
    setError(null);
    inspectNode(nodeId, config)
      .then((result) => setDetail(result.node))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [nodeId, config]);

  if (!nodeId) return null;

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-end bg-black/30 backdrop-blur-sm">
      <aside className="h-full w-full max-w-md bg-slate-950/95 border-l border-white/5 p-6 overflow-y-auto">
        <button className="text-sm text-slate-400 hover:text-white" onClick={onClose}>
          Close
        </button>
        <h2 className="text-2xl font-semibold text-white mt-2">Node detail</h2>
        {loading && <p className="text-sm text-slate-400 mt-4">Loading…</p>}
        {error && <p className="text-sm text-rose-400 mt-4">{error}</p>}
        {detail && (
          <div className="mt-4 space-y-4">
            <section className="rounded-2xl border border-white/10 p-4">
              <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Identity</p>
              <p className="text-lg text-white mt-1">{detail.nodeId}</p>
              <p className="text-sm text-slate-400">Zone: {detail.zone || 'unknown'}</p>
              <p className="text-sm text-slate-400">Status: {detail.online ? 'online' : 'offline'}</p>
              <p className="text-sm text-slate-400">Bandwidth: {detail.bandwidth ?? 'n/a'} Mbps</p>
              {detail.replicaParent && (
                <p className="text-sm text-slate-400">Replica of: {detail.replicaParent}</p>
              )}
              {detail.replicaChildren.length > 0 && (
                <p className="text-sm text-slate-400">Replicas: {detail.replicaChildren.join(', ')}</p>
              )}
            </section>

            <section className="rounded-2xl border border-white/10 p-4">
              <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Usage</p>
              <p className="text-sm text-white mt-2">
                {detail.usageBytes.used} / {detail.usageBytes.total} bytes used
              </p>
              <p className="text-xs text-slate-400 mt-1">Available: {detail.usageBytes.available}</p>
            </section>

            <section className="rounded-2xl border border-white/10 p-4">
              <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Neighbors</p>
              <p className="text-sm text-white mt-2">{detail.neighbors.join(', ') || 'None'}</p>
            </section>

            <section className="rounded-2xl border border-white/10 p-4">
              <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Stored files</p>
              <div className="mt-2 space-y-2">
                {detail.storedFiles.length === 0 && <p className="text-sm text-slate-400">No files present.</p>}
                {detail.storedFiles.map((file) => (
                  <div key={file.file_id} className="text-sm text-slate-200">
                    <p>{file.file_name}</p>
                    <p className="text-xs text-slate-500">{file.size_bytes} bytes</p>
                  </div>
                ))}
              </div>
            </section>

            <section className="rounded-2xl border border-white/10 p-4">
              <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Active transfers</p>
              <div className="mt-2 space-y-2">
                {detail.activeTransfers.length === 0 && <p className="text-sm text-slate-400">Idle</p>}
                {detail.activeTransfers.map((transfer) => (
                  <div key={transfer.file_id} className="text-sm text-slate-200">
                    <p>{transfer.file_id}</p>
                    <p className="text-xs text-slate-500">
                      {transfer.status} · {transfer.size_bytes} bytes
                    </p>
                  </div>
                ))}
              </div>
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}
