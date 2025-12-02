import { useMemo, useState } from 'react';
import { ClusterNode } from '../types';
import type {
  CloudConfig,
  DemoUploadPayload,
  NodeProvisionPayload,
  SimulationTickPayload
} from '../lib/api';
import { failNode, provisionNode, restoreNode, runSimulationTick, triggerDemoUpload } from '../lib/api';

type Props = {
  nodes: ClusterNode[];
  config: CloudConfig;
  onRefresh(): void;
};

export function ActionConsole({ nodes, config, onRefresh }: Props) {
  const [nodeId, setNodeId] = useState('');
  const [newNodeId, setNewNodeId] = useState('');
  const [zone, setZone] = useState('');
  const [uploadName, setUploadName] = useState('demo-data.bin');
  const [uploadSize, setUploadSize] = useState(5);
  const [durationSeconds, setDurationSeconds] = useState(5);
  const [status, setStatus] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const nodeOptions = useMemo(() => nodes.map((node) => node.id), [nodes]);

  const handleAction = async (label: string, fn: () => Promise<void>) => {
    try {
      setBusyAction(label);
      setStatus(null);
      await fn();
      onRefresh();
      setStatus(`${label} complete`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setStatus(`${label} failed: ${message}`);
    } finally {
      setBusyAction(null);
    }
  };

  const handleProvision = async () => {
    const payload: NodeProvisionPayload = {
      nodeId: newNodeId || undefined,
      zone: zone || undefined
    };
    await provisionNode(payload, config);
    setNewNodeId('');
    setZone('');
  };

  const handleFail = async () => {
    if (!nodeId) return;
    await failNode(nodeId, config);
  };

  const handleRestore = async () => {
    if (!nodeId) return;
    await restoreNode(nodeId, config);
  };

  const handleUpload = async () => {
    const payload: DemoUploadPayload = {
      fileName: uploadName || undefined,
      sizeMb: uploadSize
    };
    await triggerDemoUpload(payload, config);
  };

  const handleSimTick = async () => {
    const payload: SimulationTickPayload = {
      durationSeconds,
      runBackgroundJobs: true
    };
    await runSimulationTick(payload, config);
  };

  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/60 p-6 space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Control Surface</p>
        <h2 className="text-xl font-semibold text-white">Command Console</h2>
        <p className="text-sm text-slate-400 mt-1">Run safe cluster manipulations without leaving the browser.</p>
      </header>

      <div className="space-y-4">
        <h3 className="text-sm font-semibold uppercase tracking-[0.3em] text-slate-400">Provision node</h3>
        <div className="grid gap-3">
          <input
            value={newNodeId}
            onChange={(e) => setNewNodeId(e.target.value)}
            placeholder="node id (optional)"
            className="rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
          />
          <input
            value={zone}
            onChange={(e) => setZone(e.target.value)}
            placeholder="zone override (optional)"
            className="rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
          />
        </div>
        <button
          onClick={() => handleAction('Provision node', handleProvision)}
          disabled={busyAction !== null}
          className="w-full rounded-2xl bg-gradient-to-r from-indigo-500 to-sky-400 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-slate-950 disabled:opacity-50"
        >
          {busyAction === 'Provision node' ? 'Provisioning…' : 'Provision node'}
        </button>
      </div>

      <div className="space-y-3">
        <h3 className="text-sm font-semibold uppercase tracking-[0.3em] text-slate-400">Node lifecycle</h3>
        <select
          value={nodeId}
          onChange={(e) => setNodeId(e.target.value)}
          className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
        >
          <option value="">Select node…</option>
          {nodeOptions.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
        <div className="grid grid-cols-2 gap-3">
          <button
            onClick={() => handleAction('Fail node', handleFail)}
            disabled={!nodeId || busyAction !== null}
            className="rounded-2xl border border-rose-400/60 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-rose-300 disabled:opacity-40"
          >
            {busyAction === 'Fail node' ? 'Failing…' : 'Fail node'}
          </button>
          <button
            onClick={() => handleAction('Restore node', handleRestore)}
            disabled={!nodeId || busyAction !== null}
            className="rounded-2xl border border-emerald-400/60 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-emerald-300 disabled:opacity-40"
          >
            {busyAction === 'Restore node' ? 'Restoring…' : 'Restore node'}
          </button>
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="text-sm font-semibold uppercase tracking-[0.3em] text-slate-400">Demo upload</h3>
        <input
          value={uploadName}
          onChange={(e) => setUploadName(e.target.value)}
          placeholder="demo-object.bin"
          className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
        />
        <label className="flex items-center gap-3 text-xs uppercase tracking-[0.3em] text-slate-400">
          Size (MB)
          <input
            type="number"
            min={1}
            max={512}
            value={uploadSize}
            onChange={(e) => setUploadSize(Number(e.target.value))}
            className="flex-1 rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
          />
        </label>
        <button
          onClick={() => handleAction('Demo upload', handleUpload)}
          disabled={busyAction !== null}
          className="w-full rounded-2xl bg-gradient-to-r from-amber-500 to-rose-400 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-slate-950 disabled:opacity-50"
        >
          {busyAction === 'Demo upload' ? 'Uploading…' : 'Trigger upload'}
        </button>
      </div>

      <div className="space-y-3">
        <h3 className="text-sm font-semibold uppercase tracking-[0.3em] text-slate-400">Simulation</h3>
        <label className="flex items-center gap-3 text-xs uppercase tracking-[0.3em] text-slate-400">
          Duration (s)
          <input
            type="number"
            min={0.5}
            max={60}
            step={0.5}
            value={durationSeconds}
            onChange={(e) => setDurationSeconds(Number(e.target.value))}
            className="flex-1 rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
          />
        </label>
        <button
          onClick={() => handleAction('Advance simulation', handleSimTick)}
          disabled={busyAction !== null}
          className="w-full rounded-2xl bg-gradient-to-r from-emerald-500 to-cyan-400 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-slate-950 disabled:opacity-50"
        >
          {busyAction === 'Advance simulation' ? 'Advancing…' : 'Advance simulation'}
        </button>
      </div>

      {status && <p className="text-sm text-slate-300">{status}</p>}
    </section>
  );
}
