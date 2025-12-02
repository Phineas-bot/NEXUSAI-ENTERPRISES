export type ClusterNode = {
  id: string;
  zone: string;
  status: 'healthy' | 'degraded' | 'offline';
  storageUsedGb: number;
  storageCapacityGb: number;
  replicaParent?: string | null;
  isReplica?: boolean;
};

export type Transfer = {
  id: string;
  filename: string;
  progress: number;
  direction: 'upload' | 'download';
  etaSeconds: number;
};

export type TransferSummary = {
  fileId?: string;
  fileName?: string;
  sizeBytes: number;
  status: string;
  chunks: number;
  source?: string;
  target?: string;
  createdAt?: number;
  completedAt?: number;
};

export type ActivityEvent = {
  id: string;
  actor: string;
  action: string;
  target: string;
  timestamp: string;
};

export type ControllerEvent = {
  action?: string;
  timestamp?: string | number;
  target?: string;
  [key: string]: unknown;
};

export type FileEntry = {
  id: string;
  name: string;
  owner: string;
  sizeBytes: number;
  updatedAt: string;
};

export type NodeFileEntry = {
  file_id: string;
  file_name: string;
  size_bytes: number;
  completed_at?: number;
};

export type NodeTransferEntry = {
  file_id: string;
  status: string;
  size_bytes: number;
};

export type NodeDetail = {
  nodeId?: string;
  online: boolean;
  zone?: string;
  bandwidth?: number;
  replicaParent?: string;
  replicaChildren: string[];
  neighbors: string[];
  storedFiles: NodeFileEntry[];
  activeTransfers: NodeTransferEntry[];
  usageBytes: {
    used: number;
    total: number;
    available: number;
  };
  telemetry?: Record<string, unknown>;
};

export type SloPoint = {
  timestamp: string;
  burnRate: number;
};

export type AuthProfile = {
  userId: string;
  orgId: string;
  scopes: string[];
  expiresAt: string;
};

export type GrafanaPanel = {
  id: string;
  title: string;
  iframeUrl: string;
  description?: string;
};

export type ControlNodeResponse = {
  node: ClusterNode;
  message: string;
};

export type DemoUploadResponse = {
  file: FileEntry;
};

export type SimulationTickResponse = {
  duration: number;
  metrics: Record<string, number>;
};
