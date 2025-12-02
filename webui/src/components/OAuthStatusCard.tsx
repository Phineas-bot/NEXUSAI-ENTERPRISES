import { AuthProfile } from '../types';

function formatTimeUntil(timestamp: string): string {
  const diff = new Date(timestamp).getTime() - Date.now();
  if (diff <= 0) return 'expired';
  const minutes = Math.floor(diff / 60000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

type Props = {
  profile?: AuthProfile;
};

export function OAuthStatusCard({ profile }: Props) {
  if (!profile) return null;
  const expiresIn = formatTimeUntil(profile.expiresAt);
  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/60 p-6">
      <header>
        <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Security</p>
        <h2 className="text-xl font-semibold text-white">OAuth session</h2>
      </header>
      <dl className="mt-4 space-y-3 text-sm text-slate-300">
        <div className="flex items-center justify-between">
          <dt className="uppercase tracking-[0.25em] text-xs text-slate-500">User</dt>
          <dd className="text-white font-semibold">{profile.userId}</dd>
        </div>
        <div className="flex items-center justify-between">
          <dt className="uppercase tracking-[0.25em] text-xs text-slate-500">Org</dt>
          <dd className="text-white/90">{profile.orgId}</dd>
        </div>
        <div>
          <dt className="uppercase tracking-[0.25em] text-xs text-slate-500">Scopes</dt>
          <dd className="mt-1 flex flex-wrap gap-2">
            {profile.scopes.map((scope) => (
              <span key={scope} className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-3 py-1 text-xs uppercase tracking-[0.25em]">
                {scope}
              </span>
            ))}
          </dd>
        </div>
        <div className="flex items-center justify-between">
          <dt className="uppercase tracking-[0.25em] text-xs text-slate-500">Expires in</dt>
          <dd className={`text-sm font-semibold ${expiresIn === 'expired' ? 'text-rose-300' : 'text-emerald-300'}`}>{expiresIn}</dd>
        </div>
      </dl>
    </section>
  );
}
