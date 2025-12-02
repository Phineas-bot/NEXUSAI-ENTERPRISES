import type { ClusterNode } from '../types';

export function selectPreferredNode(nodes: ClusterNode[]): ClusterNode | null {
  if (!nodes.length) {
    return null;
  }
  const healthy = nodes.filter((node) => node.status === 'healthy');
  const candidates = healthy.length ? healthy : nodes;
  let best: ClusterNode | null = null;
  let bestFree = Number.NEGATIVE_INFINITY;
  for (const node of candidates) {
    const free = node.storageCapacityGb - node.storageUsedGb;
    if (free > bestFree) {
      best = node;
      bestFree = free;
    }
  }
  return best;
}
