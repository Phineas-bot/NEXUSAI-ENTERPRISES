import { useState } from 'react';
import type { CloudConfig, SimulationTickPayload, SnapshotPayload } from '../lib/api';
import { resetSimulation, restoreSnapshot, runSimulationTick, saveSnapshot } from '../lib/api';

type Props = {
  config: CloudConfig;
  onRefresh(): void;
};

export function SimulationControls({ config, onRefresh }: Props) {
  const [durationSeconds, setDurationSeconds] = useState(5);
  const [runJobs, setRunJobs] = useState(true);
  const [snapshotPath, setSnapshotPath] = useState('state.snap');
  const [restorePath, setRestorePath] = useState('state.snap');
  const [clearSaved, setClearSaved] = useState(false);
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

  const handleStep = () =>
    withStatus('Advance simulation', async () => {
      const payload: SimulationTickPayload = {
        durationSeconds,
        runBackgroundJobs: runJobs
      };
      await runSimulationTick(payload, config);
    });

  const handleReset = () =>
    withStatus('Reset simulation', async () => {
      await resetSimulation({ clearSaved }, config);
    });

  const handleSave = () =>
    withStatus('Save snapshot', async () => {
      const payload: SnapshotPayload = {
        path: snapshotPath || undefined
      };
      await saveSnapshot(payload, config);
      if (!snapshotPath) setSnapshotPath('state.snap');
    });

  const handleRestore = () =>
    withStatus('Restore snapshot', async () => {
      const payload: SnapshotPayload = {
        path: restorePath || undefined
      };
      await restoreSnapshot(payload, config);
    });

  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/60 p-6 space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Simulation</p>
        <h2 className="text-xl font-semibold text-white">Time travel controls</h2>
        <p className="text-sm text-slate-400 mt-1">Step the discrete-event sim, reset topology, or capture and restore deterministic snapshots.</p>
      </header>

      <div className="space-y-3">
        <h3 className="text-xs uppercase tracking-[0.3em] text-slate-400">Step</h3>
        <label className="text-xs uppercase tracking-[0.3em] text-slate-400 flex items-center gap-3">
          Duration (s)
          <input
            type="number"
            min={0.5}
            max={120}
            step={0.5}
            value={durationSeconds}
            onChange={(event) => setDurationSeconds(Number(event.target.value))}
            className="flex-1 rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
          />
        </label>
        <label className="flex items-center gap-3 text-xs uppercase tracking-[0.3em] text-slate-400">
          <input
            type="checkbox"
            checked={runJobs}
            onChange={(event) => setRunJobs(event.target.checked)}
            className="h-4 w-4 rounded border border-white/20 bg-slate-900"
          />
          Run background jobs
        </label>
        <button
          onClick={handleStep}
          disabled={busy}
          className="w-full rounded-2xl bg-gradient-to-r from-emerald-500 to-cyan-400 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-slate-950 disabled:opacity-50"
        >
          Step simulation
        </button>
      </div>

      <div className="space-y-3">
        <h3 className="text-xs uppercase tracking-[0.3em] text-slate-400">Reset</h3>
        <label className="flex items-center gap-3 text-xs uppercase tracking-[0.3em] text-slate-400">
          <input
            type="checkbox"
            checked={clearSaved}
            onChange={(event) => setClearSaved(event.target.checked)}
            className="h-4 w-4 rounded border border-white/20 bg-slate-900"
          />
          Clear saved state
        </label>
        <button
          onClick={handleReset}
          disabled={busy}
          className="w-full rounded-2xl border border-rose-400/60 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-rose-300 disabled:opacity-40"
        >
          Reset simulation
        </button>
      </div>

      <div className="space-y-3">
        <h3 className="text-xs uppercase tracking-[0.3em] text-slate-400">Snapshots</h3>
        <input
          value={snapshotPath}
          onChange={(event) => setSnapshotPath(event.target.value)}
          placeholder="snapshot path"
          className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
        />
        <button
          onClick={handleSave}
          disabled={busy}
          className="w-full rounded-2xl bg-gradient-to-r from-amber-500 to-rose-400 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-slate-950 disabled:opacity-50"
        >
          Save snapshot
        </button>
        <input
          value={restorePath}
          onChange={(event) => setRestorePath(event.target.value)}
          placeholder="snapshot to restore"
          className="w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-2 text-sm text-white focus:border-sky-400 focus:outline-none"
        />
        <button
          onClick={handleRestore}
          disabled={busy}
          className="w-full rounded-2xl border border-emerald-400/60 py-2 text-sm font-semibold uppercase tracking-[0.3em] text-emerald-300 disabled:opacity-40"
        >
          Restore snapshot
        </button>
      </div>

      {status && <p className="text-sm text-slate-300">{status}</p>}
    </section>
  );
}
