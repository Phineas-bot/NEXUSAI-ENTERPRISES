import { Area, AreaChart, ResponsiveContainer } from 'recharts';

type Props = {
  values: number[];
};

export function SparklineChart({ values }: Props) {
  const data = values.map((value, index) => ({ index, value }));
  return (
    <div style={{ width: '100%', height: 80 }}>
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 10, bottom: 0, left: 0, right: 0 }}>
          <defs>
            <linearGradient id="sparkline" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgb(56,189,248)" stopOpacity={0.6} />
              <stop offset="95%" stopColor="rgb(15,23,42)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area type="monotone" dataKey="value" stroke="#38bdf8" fill="url(#sparkline)" strokeWidth={2.5} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
