"""Capacity planning helper producing scaling recommendations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .base import BaseService


@dataclass
class CapacityRecommendation:
    resource: str
    action: str
    reason: str


@dataclass
class CapacityPlanner(BaseService):
    latest_recommendations: List[CapacityRecommendation] = field(default_factory=list)

    def evaluate(self, metrics_snapshot: Dict[str, float]) -> List[CapacityRecommendation]:
        recommendations: List[CapacityRecommendation] = []
        utilization = metrics_snapshot.get("storage.utilization", 0.0)
        replica_queue = metrics_snapshot.get("replication.queue_depth", 0.0)
        ingest_latency = metrics_snapshot.get("ingest.p95_ms", 0.0)

        if utilization >= 0.8:
            recommendations.append(
                CapacityRecommendation(
                    resource="storage",
                    action="add-node",
                    reason=f"hot tier utilization at {utilization:.2f}",
                )
            )
        if replica_queue >= 10:
            recommendations.append(
                CapacityRecommendation(
                    resource="replication",
                    action="scale-workers",
                    reason=f"replication backlog {replica_queue} jobs",
                )
            )
        if ingest_latency >= 2500:
            recommendations.append(
                CapacityRecommendation(
                    resource="api",
                    action="scale-gateway",
                    reason=f"ingest p95 {ingest_latency:.0f}ms",
                )
            )
        self.latest_recommendations = recommendations
        return recommendations
