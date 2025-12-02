import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { SloPoint } from '../types';

const tooltipStyle: React.CSSProperties = {
  backgroundColor: 'rgb(2,6,23)',
  border: '1px solid rgba(255,255,255,0.08)',
  borderRadius: '12px',
  color: 'white',
  padding: '12px'
};

type Props = {
  points: SloPoint[];
};

export function ObservabilityPanel({ points }: Props) {
  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/40 p-6">
      <header className="flex items-center justify-between mb-4">
        <div>
          <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Reliability</p>
          <h2 className="text-xl font-semibold text-white">Error budget burn</h2>
        </div>
        <span className="text-sm text-emerald-300">Within SLO</span>
      </header>
      <div style={{ width: '100%', height: 220 }}>
        <ResponsiveContainer>
          <LineChart data={points} margin={{ left: 0, right: 0, top: 10, bottom: 0 }}>
            <XAxis dataKey="timestamp" hide />
            <YAxis hide domain={[0, 1.6]} />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(value: number) => [`${value.toFixed(2)}×`, 'Burn rate']}
              labelFormatter={(label) => new Date(label).toLocaleString()}
            />
            <Line type="monotone" dataKey="burnRate" stroke="#38bdf8" strokeWidth={3} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
