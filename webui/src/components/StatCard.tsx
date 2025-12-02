type Props = {
  label: string;
  value: string;
  trend?: string;
  accent?: 'emerald' | 'cyan' | 'amber' | 'rose';
};

const accentMap = {
  emerald: 'from-emerald-400/20 to-emerald-500/5 border-emerald-400/50 text-emerald-300',
  cyan: 'from-sky-400/20 to-sky-500/5 border-sky-400/40 text-sky-300',
  amber: 'from-amber-400/20 to-amber-500/5 border-amber-400/40 text-amber-200',
  rose: 'from-rose-400/20 to-rose-500/5 border-rose-400/40 text-rose-200'
};

export function StatCard({ label, value, trend, accent = 'cyan' }: Props) {
  return (
    <div className={`rounded-2xl border bg-gradient-to-br ${accentMap[accent]} p-5 shadow-soft`}>
      <p className="text-sm uppercase tracking-[0.35em] text-white/70">{label}</p>
      <p className="text-3xl font-semibold text-white mt-2">{value}</p>
      {trend && <p className="text-sm text-white/80 mt-1">{trend}</p>}
    </div>
  );
}
