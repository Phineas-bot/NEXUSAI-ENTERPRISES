import { ActivityEvent, AuthProfile, ClusterNode, FileEntry, GrafanaPanel, SloPoint, Transfer } from '../types';

export const sampleNodes: ClusterNode[] = [
  { id: 'storage-az1-vn1', zone: 'usw2-az1', status: 'healthy', storageUsedGb: 54, storageCapacityGb: 80 },
  { id: 'storage-az1-vn2', zone: 'usw2-az1', status: 'degraded', storageUsedGb: 72, storageCapacityGb: 80 },
  { id: 'storage-az2-vn1', zone: 'usw2-az2', status: 'healthy', storageUsedGb: 38, storageCapacityGb: 80 },
  { id: 'storage-az3-vn1', zone: 'usw2-az3', status: 'healthy', storageUsedGb: 41, storageCapacityGb: 80 }
];

export const sampleTransfers: Transfer[] = [
  { id: 'tr-1', filename: 'Design_Sprint_v2.fig', progress: 82, direction: 'upload', etaSeconds: 35 },
  { id: 'tr-2', filename: 'Film_Reel.mov', progress: 45, direction: 'download', etaSeconds: 120 },
  { id: 'tr-3', filename: 'AI-whitepaper.pdf', progress: 100, direction: 'upload', etaSeconds: 0 }
];

export const sampleEvents: ActivityEvent[] = [
  { id: 'ev-1', actor: 'Mara', action: 'shared', target: 'Observability dashboard', timestamp: '2025-12-01T09:42:00Z' },
  { id: 'ev-2', actor: 'Phineas', action: 'restored', target: 'Marketing bundle.zip', timestamp: '2025-12-01T09:10:00Z' },
  { id: 'ev-3', actor: 'JT', action: 'promoted', target: 'Replica policy (tier-2)', timestamp: '2025-12-01T08:55:00Z' }
];

export const sampleFiles: FileEntry[] = [
  {
    id: 'file-1',
    name: 'Launch_Playbook.docx',
    owner: 'Mara',
    sizeBytes: 1_200_000,
    updatedAt: '2025-11-29T21:05:00Z',
    createdAt: '2025-11-27T08:12:00Z',
    mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  },
  {
    id: 'file-2',
    name: 'XR_Demo.mp4',
    owner: 'JT',
    sizeBytes: 2_560_000_000,
    updatedAt: '2025-11-30T14:15:00Z',
    createdAt: '2025-11-20T13:45:00Z',
    mimeType: 'video/mp4'
  },
  {
    id: 'file-3',
    name: 'OpsChecklist.xlsx',
    owner: 'Phineas',
    sizeBytes: 860_000,
    updatedAt: '2025-11-28T11:40:00Z',
    createdAt: '2025-11-18T11:40:00Z',
    mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
  }
];

export const sampleSlo: SloPoint[] = Array.from({ length: 12 }).map((_, index) => ({
  timestamp: new Date(Date.now() - (11 - index) * 60 * 60 * 1000).toISOString(),
  burnRate: Math.max(0.4, 1.2 - index * 0.06) + Math.random() * 0.1
}));

export const sampleAuthProfile: AuthProfile = {
  userId: 'user-demo@nexusai.dev',
  orgId: 'org-demo',
  scopes: ['ops.admin', 'storage.operator'],
  expiresAt: new Date(Date.now() + 55 * 60 * 1000).toISOString()
};

export const sampleGrafanaPanels: GrafanaPanel[] = [
  {
    id: 'panel-latency',
    title: 'Upload latency (p95)',
    iframeUrl: 'https://grafana.example.com/d-solo/section7?panelId=2&orgId=1',
    description: 'Live Section 7 dashboard snapshot from staging'
  },
  {
    id: 'panel-burn',
    title: 'Error budget burn',
    iframeUrl: 'https://grafana.example.com/d-solo/section7?panelId=4&orgId=1'
  }
];
