import { useState } from 'react';
import type { CloudConfig, NodeProvisionPayload } from '../lib/api';
import { provisionNode } from '../lib/api';
import type { ClusterNode } from '../types';

type Props = {
  config: CloudConfig;
  nodes: ClusterNode[];
  onRefresh(): void;
};

export function NodeAutoConnectPanel({ config, nodes, onRefresh }: Props) {
  const [nodeId, setNodeId] = useState('');
  const [zone, setZone] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleProvision = async () => {
    if (busy) return;
    const payload: NodeProvisionPayload = {
      nodeId: nodeId || undefined,
      zone: zone || undefined
    };
    try {
      setBusy(true);
      setStatus('Starting node provisioning…');
      await provisionNode(payload, config);
      onRefresh();
      const peerCount = nodes.length;
      const newLabel = payload.nodeId || 'New node';
      setStatus(`${newLabel} online and peered with ${peerCount} node${peerCount === 1 ? '' : 's'}`);
      setNodeId('');
      setZone('');
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setStatus(`Provisioning failed: ${message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/60 p-6 space-y-4">
      <header>
        <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Nodes</p>
        <h2 className="text-xl font-semibold text-white">Auto-connected provisioning</h2>
        <p className="text-sm text-slate-400 mt-1">
          Create a node and it will immediately mesh with every other node in the fabric.
        </p>
      </header>

      <input
        value={nodeId}
        onChange={(event) => setNodeId(event.target.value)}
        placeholder="node id (optional)"
        className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
      />
      <input
        value={zone}
        onChange={(event) => setZone(event.target.value)}
        placeholder="zone hint (optional)"
        className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
      />
      <button
        onClick={handleProvision}
        disabled={busy}
        className="w-full rounded-2xl bg-gradient-to-r from-indigo-500 to-sky-400 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-slate-950 disabled:opacity-50"
      >
        {busy ? 'Provisioning…' : 'Create node'}
      </button>

      <p className="text-xs text-slate-500">
        Existing peers: {nodes.length}
      </p>

      {status && <p className="text-sm text-slate-300">{status}</p>}
    </section>
  );
}
