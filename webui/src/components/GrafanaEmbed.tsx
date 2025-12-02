import { GrafanaPanel } from '../types';

type Props = {
  panels: GrafanaPanel[];
};

export function GrafanaEmbed({ panels }: Props) {
  if (!panels.length) return null;
  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/40 p-6">
      <header className="flex items-center justify-between mb-4">
        <div>
          <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Observability</p>
          <h2 className="text-xl font-semibold text-white">Grafana panels</h2>
        </div>
        <span className="text-xs text-slate-400">Embed secure Stage-7 dashboards</span>
      </header>
      <div className="grid gap-4 md:grid-cols-2">
        {panels.map((panel) => (
          <article key={panel.id} className="rounded-2xl border border-white/5 bg-slate-950/60 p-3">
            <p className="text-sm font-semibold text-white mb-2">{panel.title}</p>
            {panel.description && <p className="text-xs text-slate-400 mb-2">{panel.description}</p>}
            <div className="aspect-video rounded-xl overflow-hidden border border-white/5">
              <iframe
                src={panel.iframeUrl}
                title={panel.title}
                className="h-full w-full"
                loading="lazy"
                allow="fullscreen"
              />
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
