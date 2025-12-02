import { ClusterNode } from '../types';

type Props = {
  nodes: ClusterNode[];
  activeNodeId?: string | null;
  onSelectNode?(nodeId: string): void;
};

const statusStyles: Record<ClusterNode['status'], string> = {
  healthy: 'bg-emerald-400/20 text-emerald-200 border-emerald-500/40',
  degraded: 'bg-amber-400/20 text-amber-100 border-amber-500/40',
  offline: 'bg-rose-400/20 text-rose-100 border-rose-500/40'
};

export function ClusterTopology({ nodes, activeNodeId, onSelectNode }: Props) {
  return (
    <section className="rounded-3xl border border-white/5 bg-slate-900/40 p-6">
      <header className="flex items-center justify-between mb-6">
        <div>
          <p className="text-xs uppercase tracking-[0.35em] text-slate-400">Topology</p>
          <h2 className="text-xl font-semibold text-white">Storage virtual network</h2>
        </div>
        <span className="text-sm text-slate-400">{nodes.length} nodes across AZs</span>
      </header>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {nodes.map((node) => {
          const usage = Math.round((node.storageUsedGb / node.storageCapacityGb) * 100);
          const isActive = node.id === activeNodeId;
          const replicaLabel = node.isReplica ? `Replica of ${node.replicaParent ?? 'root'}` : 'Primary node';
          return (
            <article
              key={node.id}
              role={onSelectNode ? 'button' : undefined}
              tabIndex={onSelectNode ? 0 : undefined}
              onClick={() => onSelectNode?.(node.id)}
              onKeyDown={(event) => {
                if (!onSelectNode) return;
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelectNode(node.id);
                }
              }}
              className={`rounded-2xl border bg-slate-950/60 p-4 transition
                ${onSelectNode ? 'cursor-pointer hover:border-sky-400/60 hover:bg-slate-900/80' : ''}
                ${isActive ? 'border-sky-400/70 ring-2 ring-sky-500/40' : node.isReplica ? 'border-dashed border-white/20' : 'border-white/5'}`}
            >
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-slate-500">{node.zone}</p>
                  <p className="text-base font-semibold text-white">{node.id}</p>
                  {node.isReplica && <p className="text-xs text-slate-400">{replicaLabel}</p>}
                </div>
                <span className={`text-xs uppercase tracking-[0.3em] px-3 py-1 rounded-full border ${statusStyles[node.status]}`}>
                  {node.status}
                </span>
              </div>
              <div className="mt-4">
                <div className="flex items-center justify-between text-xs text-slate-400 mb-1">
                  <span>{node.storageUsedGb} GB / {node.storageCapacityGb} GB</span>
                  <span>{usage}% utilized</span>
                </div>
                <div className="h-2 rounded-full bg-slate-800">
                  <div className="h-full rounded-full bg-gradient-to-r from-sky-500 to-emerald-400" style={{ width: `${usage}%` }} />
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
