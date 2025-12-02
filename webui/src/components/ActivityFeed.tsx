import { ActivityEvent } from '../types';
import { formatRelative } from '../utils/formatters';

type Props = {
  events: ActivityEvent[];
};

export function ActivityFeed({ events }: Props) {
  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/40 p-6">
      <header className="flex items-center justify-between mb-6">
        <div>
          <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Signals</p>
          <h2 className="text-xl font-semibold text-white">Activity feed</h2>
        </div>
        <button className="text-sm text-slate-300 hover:text-white">Export log</button>
      </header>
      <ol className="space-y-4">
        {events.map((event) => (
          <li key={event.id} className="flex items-start gap-4">
            <span className="mt-1 h-2 w-2 rounded-full bg-emerald-400" />
            <div className="flex-1 border-b border-white/5 pb-3">
              <p className="text-sm text-white">
                <strong className="text-white/90">{event.actor}</strong> {event.action} <strong className="text-white/90">{event.target}</strong>
              </p>
              <p className="text-xs text-slate-400 mt-1">{formatRelative(event.timestamp)}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
