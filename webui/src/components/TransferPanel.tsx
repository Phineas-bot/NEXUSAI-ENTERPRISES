import { Transfer } from '../types';

type Props = {
  transfers: Transfer[];
};

const directionMap = {
  upload: { label: 'Upload', color: 'text-emerald-200 bg-emerald-400/10 border-emerald-400/30' },
  download: { label: 'Download', color: 'text-sky-200 bg-sky-400/10 border-sky-400/30' }
};

export function TransferPanel({ transfers }: Props) {
  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/40 p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Pipelines</p>
          <h2 className="text-xl font-semibold text-white">Live transfers</h2>
        </div>
        <button className="text-sm text-slate-300 hover:text-white">View history →</button>
      </div>
      <div className="space-y-4">
        {transfers.map((transfer) => {
          const direction = directionMap[transfer.direction];
          return (
            <article key={transfer.id} className="border border-white/5 rounded-2xl p-4 bg-slate-950/60">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-slate-400">{direction.label}</p>
                  <p className="text-base font-semibold text-white">{transfer.filename}</p>
                </div>
                <span className={`text-xs uppercase tracking-[0.3em] px-3 py-1 rounded-full border ${direction.color}`}>
                  {transfer.progress}%
                </span>
              </div>
              <div className="mt-4">
                <div className="h-2 rounded-full bg-slate-800">
                  <div className="h-full rounded-full bg-gradient-to-r from-emerald-400 to-sky-400" style={{ width: `${transfer.progress}%` }} />
                </div>
                <p className="text-xs text-slate-400 mt-2">ETA: {Math.max(0, transfer.etaSeconds)}s</p>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
