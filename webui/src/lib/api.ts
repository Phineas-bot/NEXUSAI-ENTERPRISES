import {
  ActivityEvent,
  AuthProfile,
  ClusterNode,
  ControlNodeResponse,
  ControllerEvent,
  DemoUploadResponse,
  FileEntry,
  GrafanaPanel,
  NodeDetail,
  SimulationTickResponse,
  SloPoint,
  Transfer,
  TransferSummary
} from '../types';
import {
  sampleAuthProfile,
  sampleEvents,
  sampleFiles,
  sampleGrafanaPanels,
  sampleNodes,
  sampleSlo,
  sampleTransfers
} from './sampleData';

export type CloudConfig = {
  restBase: string;
  authToken?: string;
  userRoles?: string;
};

export type ClusterMap = Record<string, string[]>;

const defaultConfig: CloudConfig = {
  restBase: 'http://localhost:8000'
};

const headers = (config: CloudConfig) => ({
  'Content-Type': 'application/json',
  ...(config.authToken ? { Authorization: `Bearer ${config.authToken}` } : {}),
  ...(config.userRoles ? { 'X-User-Roles': config.userRoles } : {})
});

const authHeaders = (config: CloudConfig) => ({
  ...(config.authToken ? { Authorization: `Bearer ${config.authToken}` } : {}),
  ...(config.userRoles ? { 'X-User-Roles': config.userRoles } : {})
});

async function postJson<T>(path: string, body: unknown, config: CloudConfig): Promise<T> {
  const url = `${config.restBase}${path}`;
  const response = await fetch(url, {
    method: 'POST',
    headers: headers(config),
    body: body ? JSON.stringify(body) : '{}'
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

async function deleteJson<T>(path: string, config: CloudConfig): Promise<T> {
  const url = `${config.restBase}${path}`;
  const response = await fetch(url, {
    method: 'DELETE',
    headers: headers(config)
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

async function fetchJson<T>(path: string, config: CloudConfig): Promise<T> {
  const url = `${config.restBase}${path}`;
  const response = await fetch(url, {
    headers: headers(config)
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function getClusterNodes(config: CloudConfig = defaultConfig): Promise<ClusterNode[]> {
  try {
    return await fetchJson<ClusterNode[]>(`/v1/storage/nodes?include_replicas=1`, config);
  } catch {
    return sampleNodes;
  }
}

export async function uploadRealFile(
  file: File,
  sourceNode?: string,
  config: CloudConfig = defaultConfig
): Promise<{ datasetId: string; targetNode: string }> {
  const url = `${config.restBase}/v1/files/upload-real`;
  const formData = new FormData();
  if (sourceNode) {
    formData.append('source_node', sourceNode);
  }
  formData.append('file', file);
  const response = await fetch(url, {
    method: 'POST',
    headers: authHeaders(config),
    body: formData
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || 'Upload failed');
  }
  const payload = (await response.json()) as { datasetId: string; targetNode: string };
  return payload;
}

export async function downloadRealFile(datasetId: string, config: CloudConfig = defaultConfig): Promise<Blob> {
  const url = `${config.restBase}/v1/files/download-real/${datasetId}`;
  const response = await fetch(url, {
    headers: authHeaders(config)
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || 'Download failed');
  }
  return await response.blob();
}

export async function getTransfers(config: CloudConfig = defaultConfig): Promise<Transfer[]> {
  try {
    return await fetchJson<Transfer[]>(`/v1/transfers`, config);
  } catch {
    return sampleTransfers;
  }
}

export async function getRecentFiles(config: CloudConfig = defaultConfig): Promise<FileEntry[]> {
  try {
    return await fetchJson<FileEntry[]>(`/v1/files?limit=25`, config);
  } catch {
    return sampleFiles;
  }
}

export async function getActivity(config: CloudConfig = defaultConfig): Promise<ActivityEvent[]> {
  try {
    return await fetchJson<ActivityEvent[]>(`/v1/activity?limit=10`, config);
  } catch {
    return sampleEvents;
  }
}

export async function getSlo(config: CloudConfig = defaultConfig): Promise<SloPoint[]> {
  try {
    return await fetchJson<SloPoint[]>(`/v1/observability/slo/burn-rate`, config);
  } catch {
    return sampleSlo;
  }
}

export async function getAuthProfile(config: CloudConfig = defaultConfig): Promise<AuthProfile> {
  try {
    return await fetchJson<AuthProfile>(`/v1/auth/profile`, config);
  } catch {
    return sampleAuthProfile;
  }
}

export async function getGrafanaPanels(config: CloudConfig = defaultConfig): Promise<GrafanaPanel[]> {
  try {
    return await fetchJson<GrafanaPanel[]>(`/v1/observability/grafana/panels`, config);
  } catch {
    return sampleGrafanaPanels;
  }
}

export type NodeProvisionPayload = {
  nodeId?: string;
  storageGb?: number;
  bandwidthMbps?: number;
  cpuCapacity?: number;
  memoryCapacity?: number;
  zone?: string;
};

export type DemoUploadPayload = {
  fileName?: string;
  sizeMb?: number;
  parentId?: string;
  sourceNode?: string;
};

export type SimulationTickPayload = {
  durationSeconds?: number;
  runBackgroundJobs?: boolean;
};

export type LinkPayload = {
  nodeA: string;
  nodeB: string;
  bandwidthMbps?: number;
  latencyMs?: number;
};

export type LinkPairPayload = {
  nodeA: string;
  nodeB: string;
};

export type FilePushPayload = {
  sourceNode: string;
  fileName?: string;
  sizeMb?: number;
  preferLocal?: boolean;
};

export type FileTransferPayload = {
  sourceNode: string;
  targetNode: string;
  fileName: string;
  sizeMb?: number;
};

export type FileFetchPayload = {
  targetNode: string;
  fileName: string;
};

export type SnapshotPayload = {
  path?: string;
};

export async function provisionNode(payload: NodeProvisionPayload, config: CloudConfig = defaultConfig) {
  return postJson<ControlNodeResponse>(`/v1/control/nodes`, payload, config);
}

export async function failNode(nodeId: string, config: CloudConfig = defaultConfig) {
  return postJson<{ node_id: string; status: string }>(`/v1/control/nodes/${nodeId}:fail`, {}, config);
}

export async function restoreNode(nodeId: string, config: CloudConfig = defaultConfig) {
  return postJson<{ node_id: string; status: string }>(`/v1/control/nodes/${nodeId}:restore`, {}, config);
}

export async function triggerDemoUpload(payload: DemoUploadPayload, config: CloudConfig = defaultConfig) {
  return postJson<DemoUploadResponse>(`/v1/control/uploads/demo`, payload, config);
}

export async function runSimulationTick(payload: SimulationTickPayload, config: CloudConfig = defaultConfig) {
  return postJson<SimulationTickResponse>(`/v1/control/sim/tick`, payload, config);
}

export async function removeNode(nodeId: string, config: CloudConfig = defaultConfig) {
  return deleteJson<{ node_id: string; removed: boolean }>(`/v1/control/nodes/${nodeId}`, config);
}

export async function inspectNode(nodeId: string, config: CloudConfig = defaultConfig) {
  return fetchJson<{ node: NodeDetail }>(`/v1/control/nodes/${nodeId}`, config);
}

export async function getClusters(config: CloudConfig = defaultConfig) {
  return fetchJson<ClusterMap>(`/v1/control/clusters`, config);
}

export async function getControllerEvents(limit = 25, config: CloudConfig = defaultConfig) {
  return fetchJson<ControllerEvent[]>(`/v1/control/events?limit=${limit}`, config);
}

export async function connectNodesLink(payload: LinkPayload, config: CloudConfig = defaultConfig) {
  return postJson<{ connected: boolean }>(`/v1/control/links`, payload, config);
}

export async function disconnectNodesLink(payload: LinkPairPayload, config: CloudConfig = defaultConfig) {
  return postJson<{ disconnected: boolean }>(`/v1/control/links:disconnect`, payload, config);
}

export async function failLink(payload: LinkPairPayload, config: CloudConfig = defaultConfig) {
  return postJson<{ failed: boolean }>(`/v1/control/links:fail`, payload, config);
}

export async function restoreLink(payload: LinkPairPayload, config: CloudConfig = defaultConfig) {
  return postJson<{ restored: boolean }>(`/v1/control/links:restore`, payload, config);
}

export async function pushFile(payload: FilePushPayload, config: CloudConfig = defaultConfig) {
  return postJson<{ transfer: TransferSummary; targetNode: string }>(`/v1/control/transfers/push`, payload, config);
}

export async function initiateTransfer(payload: FileTransferPayload, config: CloudConfig = defaultConfig) {
  return postJson<{ transfer: TransferSummary }>(`/v1/control/transfers/initiate`, payload, config);
}

export async function fetchFile(payload: FileFetchPayload, config: CloudConfig = defaultConfig) {
  return postJson<{ transfer: TransferSummary }>(`/v1/control/transfers/fetch`, payload, config);
}

export async function resetSimulation(payload: { clearSaved?: boolean }, config: CloudConfig = defaultConfig) {
  return postJson<{ status: string }>(`/v1/control/sim/reset`, payload, config);
}

export async function saveSnapshot(payload: SnapshotPayload, config: CloudConfig = defaultConfig) {
  return postJson<{ path: string }>(`/v1/control/sim/save`, payload, config);
}

export async function restoreSnapshot(payload: SnapshotPayload, config: CloudConfig = defaultConfig) {
  return postJson<{ restored: boolean }>(`/v1/control/sim/restore`, payload, config);
}
