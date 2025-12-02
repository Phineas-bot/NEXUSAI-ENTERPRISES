import { useEffect, useMemo, useState } from 'react';
import { AppHeader } from './components/AppHeader';
import { StatCard } from './components/StatCard';
import { ClusterTopology } from './components/ClusterTopology';
import { FileExplorer } from './components/FileExplorer';
import { TransferPanel } from './components/TransferPanel';
import { ActivityFeed } from './components/ActivityFeed';
import { NodeInspector } from './components/NodeInspector';
import { NodeAutoConnectPanel } from './components/NodeAutoConnectPanel';
import { FileUploadPanel } from './components/FileUploadPanel';
import { FileDownloadPanel } from './components/FileDownloadPanel';
import { ActivityEvent, ClusterNode, FileEntry, SloPoint, Transfer } from './types';
import {
  CloudConfig,
  getActivity,
  getClusterNodes,
  getFileCatalog,
  getRecentFiles,
  getSlo,
  getTransfers
} from './lib/api';

function useDashboardData(config: CloudConfig) {
  const [nodes, setNodes] = useState<ClusterNode[]>([]);
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [catalog, setCatalog] = useState<FileEntry[]>([]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [slo, setSlo] = useState<SloPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshIndex, setRefreshIndex] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    async function load() {
      try {
        const [nodesData, transfersData, filesData, catalogData, eventsData, sloData] = await Promise.all([
          getClusterNodes(config),
          getTransfers(config),
          getRecentFiles(config),
          getFileCatalog(config),
          getActivity(config),
          getSlo(config)
        ]);
        if (!cancelled) {
          setNodes(nodesData);
          setTransfers(transfersData);
          setFiles(filesData);
          setCatalog(catalogData);
          setEvents(eventsData);
          setSlo(sloData);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [config.restBase, config.authToken, refreshIndex]);

  const refresh = () => setRefreshIndex((index) => index + 1);

  return { nodes, transfers, files, catalog, events, slo, loading, refresh };
}

export default function App() {
  const [config] = useState<CloudConfig>({ restBase: 'http://localhost:8000', userRoles: 'ops.admin' });
  const { nodes, transfers, files, catalog, events, slo, loading, refresh } = useDashboardData(config);
  const [inspectorNodeId, setInspectorNodeId] = useState<string | null>(null);

  useEffect(() => {
    if (inspectorNodeId && !nodes.find((node) => node.id === inspectorNodeId)) {
      setInspectorNodeId(null);
    }
  }, [nodes, inspectorNodeId]);

  const stats = useMemo(() => {
    const totalCapacity = nodes.reduce((sum, node) => sum + node.storageCapacityGb, 0);
    const totalUsed = nodes.reduce((sum, node) => sum + node.storageUsedGb, 0);
    const activeAlerts = nodes.filter((node) => node.status !== 'healthy').length;
    const dataFootprint = files.reduce((sum, file) => sum + file.sizeBytes, 0);
    return {
      utilization: totalCapacity ? `${Math.round((totalUsed / totalCapacity) * 100)}%` : '—',
      transfers: `${transfers.length} active`,
      footprint: `${(dataFootprint / 1_000_000_000).toFixed(2)} GB`,
      alerts: `${activeAlerts} alerts`
    };
  }, [nodes, transfers.length, files]);

  const sloSeries = slo.map((point) => point.burnRate);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 text-white">
      <main className="mx-auto max-w-7xl px-4 py-10 lg:px-10 lg:py-12 space-y-10">
        <AppHeader sloSeries={sloSeries} />

        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <StatCard label="Capacity" value={stats.utilization} trend="Global storage utilization" accent="cyan" />
          <StatCard label="Transfers" value={stats.transfers} trend="Pipelines running" accent="emerald" />
          <StatCard label="Data footprint" value={stats.footprint} trend="Tracked across tenants" accent="amber" />
          <StatCard label="Reliability" value={stats.alerts} trend="Nodes needing attention" accent="rose" />
        </section>

        <section className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2 space-y-6">
            <ClusterTopology
              nodes={nodes}
              activeNodeId={inspectorNodeId}
              onSelectNode={(nodeId) => setInspectorNodeId(nodeId)}
            />
            <FileExplorer recentFiles={files} catalogFiles={catalog} />
          </div>
          <div className="space-y-6">
            <NodeAutoConnectPanel config={config} nodes={nodes} onRefresh={refresh} />
            <FileUploadPanel config={config} nodes={nodes} onRefresh={refresh} />
            <FileDownloadPanel config={config} />
            <TransferPanel transfers={transfers} />
          </div>
        </section>

        <section className="space-y-6">
          <ActivityFeed events={events} />
        </section>

        {loading && (
          <div className="fixed inset-0 pointer-events-none bg-slate-950/40 backdrop-blur-sm flex items-center justify-center">
            <div className="rounded-3xl border border-white/10 bg-slate-900/80 px-8 py-6 shadow-soft">
              <p className="text-sm uppercase tracking-[0.35em] text-slate-400 text-center">Syncing telemetry…</p>
            </div>
          </div>
        )}
      </main>
      <NodeInspector
        nodeId={inspectorNodeId}
        config={config}
        onClose={() => setInspectorNodeId(null)}
        onRefresh={refresh}
      />
    </div>
  );
}
