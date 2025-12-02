import { useState } from 'react';
import type { CloudConfig } from '../lib/api';

type Props = {
  onChange(config: CloudConfig): void;
};

export function ControlPanel({ onChange }: Props) {
  const [restBase, setRestBase] = useState('http://localhost:8000');
  const [token, setToken] = useState('');
  const [roles, setRoles] = useState('ops.admin');

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    onChange({ restBase, authToken: token || undefined, userRoles: roles || undefined });
  }

  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/60 p-6">
      <header className="mb-4">
        <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Control</p>
        <h2 className="text-xl font-semibold text-white">Environment</h2>
        <p className="text-sm text-slate-400 mt-1">Point the UI at any CloudSim cluster by dropping in the REST base and optional token.</p>
      </header>
      <form className="space-y-4" onSubmit={handleSubmit}>
        <div>
          <label className="text-xs uppercase tracking-[0.3em] text-slate-400">REST base URL</label>
          <input
            value={restBase}
            onChange={(e) => setRestBase(e.target.value)}
            placeholder="https://staging.api"
            className="mt-2 w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-3 text-white focus:border-sky-400 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-xs uppercase tracking-[0.3em] text-slate-400">Auth token</label>
          <input
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Bearer token (optional)"
            className="mt-2 w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-3 text-white focus:border-sky-400 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-xs uppercase tracking-[0.3em] text-slate-400">User roles</label>
          <input
            value={roles}
            onChange={(e) => setRoles(e.target.value)}
            placeholder="ops.admin, storage.operator"
            className="mt-2 w-full rounded-2xl border border-white/10 bg-slate-950/80 px-4 py-3 text-white focus:border-sky-400 focus:outline-none"
          />
        </div>
        <button
          type="submit"
          className="w-full rounded-2xl bg-gradient-to-r from-sky-500 to-emerald-400 py-3 text-sm font-semibold uppercase tracking-[0.35em] text-slate-950"
        >
          Update target
        </button>
      </form>
    </section>
  );
}
