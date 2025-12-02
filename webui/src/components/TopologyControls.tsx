import { useMemo, useState } from 'react';
import type { CloudConfig, LinkPayload, LinkPairPayload, NodeProvisionPayload } from '../lib/api';
import {
  connectNodesLink,
  disconnectNodesLink,
  failLink,
  provisionNode,
  removeNode,
  restoreLink
} from '../lib/api';
import type { ClusterNode } from '../types';

type Props = {
  nodes: ClusterNode[];
  config: CloudConfig;
  onRefresh(): void;
};

export function TopologyControls({ nodes, config, onRefresh }: Props) {
  const [newNodeId, setNewNodeId] = useState('');
  const [zone, setZone] = useState('');
  const [linkA, setLinkA] = useState('');
  const [linkB, setLinkB] = useState('');
  const [bw, setBw] = useState<number | undefined>(undefined);
  const [latency, setLatency] = useState<number | undefined>(undefined);
  const [targetNode, setTargetNode] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<boolean>(false);

  const nodeOptions = useMemo(() => nodes.map((node) => node.id), [nodes]);

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

  const handleProvision = () =>
    withStatus('Provision node', async () => {
      const payload: NodeProvisionPayload = {
        nodeId: newNodeId || undefined,
        zone: zone || undefined
      };
      await provisionNode(payload, config);
      setNewNodeId('');
      setZone('');
    });

  const handleRemove = () =>
    withStatus('Remove node', async () => {
      if (!targetNode) throw new Error('Select a node');
      await removeNode(targetNode, config);
      setTargetNode('');
    });

  const handleLinkAction = (label: string, fn: (payload: LinkPayload | LinkPairPayload) => Promise<unknown>) =>
    withStatus(label, async () => {
      if (!linkA || !linkB) throw new Error('Select both nodes');
      await fn({ nodeA: linkA, nodeB: linkB, bandwidthMbps: bw, latencyMs: latency } as LinkPayload);
    });

  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/60 p-6 space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Topology controls</p>
        <h2 className="text-xl font-semibold text-white">Network configuration</h2>
        <p className="text-sm text-slate-400 mt-1">Provision nodes and manage cross-links between availability zones.</p>
      </header>

      <div className="space-y-3">
        <h3 className="text-xs uppercase tracking-[0.3em] text-slate-400">Provision node</h3>
        <input
          value={newNodeId}
          onChange={(e) => setNewNodeId(e.target.value)}
          placeholder="node-id (optional)"
          className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
        />
        <input
          value={zone}
          onChange={(e) => setZone(e.target.value)}
          placeholder="zone override"
          className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
        />
        <button
          onClick={handleProvision}
          disabled={busy}
          className="w-full rounded-2xl bg-gradient-to-r from-indigo-500 to-sky-400 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-slate-950 disabled:opacity-50"
        >
          Provision
        </button>
      </div>

      <div className="space-y-3">
        <h3 className="text-xs uppercase tracking-[0.3em] text-slate-400">Remove node</h3>
        <select
          value={targetNode}
          onChange={(e) => setTargetNode(e.target.value)}
          className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-rose-400 focus:outline-none"
        >
          <option value="">Select node…</option>
          {nodeOptions.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
        <button
          onClick={handleRemove}
          disabled={!targetNode || busy}
          className="w-full rounded-2xl border border-rose-400/60 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-rose-300 disabled:opacity-40"
        >
          Remove
        </button>
      </div>

      <div className="space-y-3">
        <h3 className="text-xs uppercase tracking-[0.3em] text-slate-400">Link management</h3>
        <div className="grid grid-cols-2 gap-3">
          <select
            value={linkA}
            onChange={(e) => setLinkA(e.target.value)}
            className="rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
          >
            <option value="">Node A</option>
            {nodeOptions.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
          <select
            value={linkB}
            onChange={(e) => setLinkB(e.target.value)}
            className="rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
          >
            <option value="">Node B</option>
            {nodeOptions.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <input
            type="number"
            placeholder="Bandwidth Mbps"
            value={bw ?? ''}
            onChange={(e) => setBw(e.target.value ? Number(e.target.value) : undefined)}
            className="rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
          />
          <input
            type="number"
            placeholder="Latency ms"
            value={latency ?? ''}
            onChange={(e) => setLatency(e.target.value ? Number(e.target.value) : undefined)}
            className="rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <button
            onClick={() => handleLinkAction('Connect link', (payload) => connectNodesLink(payload as LinkPayload, config))}
            disabled={busy}
            className="rounded-2xl bg-gradient-to-r from-emerald-600 to-sky-500 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-slate-950 disabled:opacity-40"
          >
            Connect
          </button>
          <button
            onClick={() => handleLinkAction('Disconnect link', (payload) => disconnectNodesLink(payload as LinkPairPayload, config))}
            disabled={busy}
            className="rounded-2xl border border-amber-400/60 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-amber-300 disabled:opacity-40"
          >
            Disconnect
          </button>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <button
            onClick={() => handleLinkAction('Fail link', (payload) => failLink(payload as LinkPairPayload, config))}
            disabled={busy}
            className="rounded-2xl border border-rose-400/60 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-rose-300 disabled:opacity-40"
          >
            Fail
          </button>
          <button
            onClick={() => handleLinkAction('Restore link', (payload) => restoreLink(payload as LinkPairPayload, config))}
            disabled={busy}
            className="rounded-2xl border border-emerald-400/60 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-emerald-300 disabled:opacity-40"
          >
            Restore
          </button>
        </div>
      </div>

      {status && <p className="text-sm text-slate-300">{status}</p>}
    </section>
  );
}
