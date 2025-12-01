"""Observability manager providing dashboards, alerts, and SLO checks."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..telemetry import TelemetryCollector
from ..config import CloudDriveConfig
from .base import BaseService


@dataclass
class SLODefinition:
    name: str
    threshold: float
    window_minutes: int
    metric: str
    comparator: str = ">="


@dataclass
class ObservabilityManager(BaseService):
    dashboards: Dict[str, dict] = field(default_factory=dict)
    slo_definitions: List[SLODefinition] = field(default_factory=list)
    recent_alerts: List[dict] = field(default_factory=list)
    _state_path: Path | None = field(default=None, init=False, repr=False)
    _suspend_persistence: bool = field(default=False, init=False, repr=False)
    _state_cipher: "_StateCipher" | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        state_path = getattr(self.config.observability, "state_path", None)
        cfg = getattr(self.config, "observability", None)
        if state_path:
            self._state_path = Path(state_path).expanduser()
        encryption_key = getattr(cfg, "state_encryption_key", None) if cfg else None
        if encryption_key:
            self._state_cipher = _StateCipher(encryption_key)
        self._load_state()
        self._apply_config_defaults()

    def register_dashboard(self, dashboard_id: str, definition: dict) -> dict:
        self.dashboards[dashboard_id] = definition
        self._persist_state()
        return definition

    def remove_dashboard(self, dashboard_id: str) -> bool:
        removed = self.dashboards.pop(dashboard_id, None) is not None
        if removed:
            self._persist_state()
        return removed

    def upsert_slo(self, slo: SLODefinition) -> SLODefinition:
        for idx, existing in enumerate(self.slo_definitions):
            if existing.name == slo.name:
                self.slo_definitions[idx] = slo
                self._persist_state()
                return slo
        self.slo_definitions.append(slo)
        self._persist_state()
        return slo

    def remove_slo(self, name: str) -> bool:
        for idx, existing in enumerate(self.slo_definitions):
            if existing.name == name:
                self.slo_definitions.pop(idx)
                self._persist_state()
                return True
        return False

    def evaluate_slos(self, metrics_snapshot: Dict[str, float]) -> List[dict]:
        alerts: List[dict] = []
        for slo in self.slo_definitions:
            value = metrics_snapshot.get(slo.metric)
            if value is None:
                continue
            if self._compare(value, slo.threshold, slo.comparator):
                alert = {
                    "slo": slo.name,
                    "metric": slo.metric,
                    "value": value,
                    "threshold": slo.threshold,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                alerts.append(alert)
                self.recent_alerts.append(alert)
                self.telemetry.emit_event("slo_violation", alert)
        return alerts

    def bootstrap_defaults(self) -> None:
        default_dashboards = {
            "ingest_performance": {
                "title": "Upload Experience",
                "widgets": [
                    {"type": "timeseries", "metric": "ingest.p95_ms", "threshold": 2000},
                    {"type": "counter", "metric": "upload_finalize", "label": "Uploads/min"},
                ],
            },
            "storage_health": {
                "title": "Storage & Replication",
                "widgets": [
                    {"type": "gauge", "metric": "storage.utilization", "threshold": 0.8},
                    {"type": "table", "metric": "replication.queue_depth", "label": "Replica Queue"},
                ],
            },
        }
        changed = False
        for dashboard_id, definition in default_dashboards.items():
            if dashboard_id not in self.dashboards:
                self.dashboards[dashboard_id] = definition
                changed = True

        default_slos = [
            SLODefinition(
                name="upload_latency",
                metric="ingest.p95_ms",
                threshold=2000,
                comparator=">=",
                window_minutes=15,
            ),
            SLODefinition(
                name="storage_capacity_safety",
                metric="storage.utilization",
                threshold=0.85,
                comparator=">=",
                window_minutes=30,
            ),
            SLODefinition(
                name="replication_backlog",
                metric="replication.queue_depth",
                threshold=10,
                comparator=">=",
                window_minutes=10,
            ),
        ]
        for slo in default_slos:
            if not self._has_slo(slo.name):
                self.slo_definitions.append(slo)
                changed = True
        if changed:
            self._persist_state()

    @staticmethod
    def _compare(lhs: float, rhs: float, comparator: str) -> bool:
        if comparator == ">=":
            return lhs >= rhs
        if comparator == ">":
            return lhs > rhs
        if comparator == "<=":
            return lhs <= rhs
        if comparator == "<":
            return lhs < rhs
        if comparator == "==":
            return lhs == rhs
        return False

    # Persistence helpers -------------------------------------------------

    def _apply_config_defaults(self) -> None:
        cfg = getattr(self.config, "observability", None)
        if not cfg:
            return
        dashboards = getattr(cfg, "dashboards", {}) or {}
        slos = getattr(cfg, "slo_definitions", []) or []
        changed = False
        for dashboard_id, definition in dashboards.items():
            if dashboard_id not in self.dashboards:
                self.dashboards[dashboard_id] = definition
                changed = True
        for slo_data in slos:
            try:
                slo = SLODefinition(**slo_data)
            except TypeError:
                continue
            if not self._has_slo(slo.name):
                self.slo_definitions.append(slo)
                changed = True
        if changed:
            self._persist_state()

    def _load_state(self) -> None:
        if not self._state_path or not self._state_path.exists():
            return
        raw_bytes: Optional[bytes] = None
        try:
            raw_bytes = self._state_path.read_bytes()
        except OSError:
            return
        payload = self._decode_payload(raw_bytes)
        if payload is None:
            return
        self._suspend_persistence = True
        try:
            dashboards = payload.get("dashboards")
            if isinstance(dashboards, dict):
                self.dashboards.update(dashboards)
            for slo_data in payload.get("slos", []):
                try:
                    slo = SLODefinition(**slo_data)
                except TypeError:
                    continue
                if not self._has_slo(slo.name):
                    self.slo_definitions.append(slo)
        finally:
            self._suspend_persistence = False

    def _persist_state(self) -> None:
        if self._suspend_persistence or not self._state_path:
            return
        payload = {
            "dashboards": self.dashboards,
            "slos": [asdict(slo) for slo in self.slo_definitions],
        }
        encoded = self._encode_payload(payload)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._state_path.with_suffix(".tmp")
        tmp_path.write_bytes(encoded)
        tmp_path.replace(self._state_path)

    def _has_slo(self, name: str) -> bool:
        return any(existing.name == name for existing in self.slo_definitions)

    def _decode_payload(self, raw_bytes: bytes) -> Optional[dict]:
        try:
            decoded = self._state_cipher.decrypt(raw_bytes) if self._state_cipher else raw_bytes
            return json.loads(decoded.decode("utf-8"))
        except Exception as exc:  # pragma: no cover - corruption path
            self.telemetry.emit_event("observability_state_corrupt", detail=str(exc))
            return None

    def _encode_payload(self, payload: dict) -> bytes:
        raw = json.dumps(payload, indent=2).encode("utf-8")
        return self._state_cipher.encrypt(raw) if self._state_cipher else raw


class _StateCipher:
    def __init__(self, secret: str) -> None:
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("State encryption requires the 'cryptography' package") from exc
        token = self._normalize(secret)
        self._fernet = Fernet(token)

    def encrypt(self, payload: bytes) -> bytes:
        return self._fernet.encrypt(payload)

    def decrypt(self, payload: bytes) -> bytes:
        return self._fernet.decrypt(payload)

    @staticmethod
    def _normalize(secret: str) -> bytes:
        try:
            data = base64.urlsafe_b64decode(secret)
            if len(data) == 32:
                return secret.encode()
        except Exception:
            pass
        digest = hashlib.sha256(secret.encode()).digest()
        return base64.urlsafe_b64encode(digest)
