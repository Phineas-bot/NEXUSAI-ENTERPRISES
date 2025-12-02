import { SparklineChart } from './SparklineChart';

type Props = {
  sloSeries: number[];
};

export function AppHeader({ sloSeries }: Props) {
  return (
    <header className="bg-slate-900/60 backdrop-blur-lg border border-white/5 rounded-3xl px-8 py-6 shadow-soft">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-sm uppercase tracking-[0.35em] text-slate-400 font-semibold">Nexus CloudSim</p>
          <h1 className="text-3xl md:text-4xl font-semibold text-white mt-2">Control Center</h1>
          <p className="text-slate-400 mt-2 max-w-2xl">
            Operate your simulated enterprise cloud from a single glass pane: clusters, replicas, transfers, observability, and governance in one place.
          </p>
        </div>
        <div className="bg-slate-900 border border-white/5 rounded-2xl px-6 py-4 w-full max-w-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase tracking-widest text-slate-400">SLO Burn Rate</span>
            <span className="text-lg font-semibold text-white">0.73×</span>
          </div>
          <div className="mt-3">
            <SparklineChart values={sloSeries} />
          </div>
          <p className="text-[13px] text-emerald-400 mt-2">+5% healthier vs last 24h</p>
        </div>
      </div>
    </header>
  );
}
