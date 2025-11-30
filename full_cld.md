## Cloud Storage Build-Out Prompt TODO

1. **Foundation & Architecture**
	- Prompt: “Describe the end-to-end architecture for a Google Drive–style system using CloudSim’s virtual infrastructure. Cover client apps, API gateways, control plane, metadata DB, storage fabric, and observability mesh, and explain how data flows during upload/download.”
	- Response:
		1. **Client tier**: Web/mobile/CLI apps authenticate via OIDC, maintain local caches, and talk exclusively to the API gateway over TLS. Uploads use resumable sessions; downloads can stream from nearest edge.
		2. **API gateway**: Terminates TLS, performs rate limiting and auth token validation, and forwards requests to control-plane microservices (metadata, sharing, activity). It exposes both REST and gRPC endpoints with consistent error envelopes.
		3. **Control plane**: Built atop the existing CloudSim controller. Services include Metadata Service (file/folder CRUD), Upload Orchestrator (creates sessions, breaks payloads into chunks, schedules ingestion jobs), Sharing Service (ACL evaluation), Activity Service (event fan-out). They persist state in a relational metadata DB and enqueue work onto a durable message bus for background workers.
		4. **Storage fabric**: The CloudSim storage virtual network acts as the data plane. The chunk router selects target nodes, leverages manifests for spillover, and invokes ingestion on StorageVirtualNode instances. Replica Manager enforces placement policies and handles healing. A manifest store links logical files to distributed chunks.
		5. **Observability mesh**: Every component emits metrics (Prometheus/OpenTelemetry), structured logs, and traces correlated by request IDs. A monitoring stack (Prometheus + Tempo + Grafana) visualizes latency, throughput, and error budgets; alerts feed PagerDuty.
		6. **Upload flow**: Client requests session → gateway → Upload Orchestrator allocates manifest, returns chunk URLs → client uploads chunks directly (or via signed URLs) → chunk router pushes data into CloudSim nodes → completion events update metadata DB and activity log → notification service informs subscribers.
		7. **Download flow**: Client requests download → gateway verifies ACL → Metadata Service resolves manifests → Download Coordinator selects optimal node(s) → issues signed URLs or streams via gateway → transfer progress reported back for activity logging.
	- Prompt: “Define the core data models (users, orgs, devices, nodes, files, versions, shares, audit events) plus persistence choices (relational schema for metadata, object store layout for chunks/manifests, cache layers). Produce ERDs and schema migration steps.”
	- Response:
		- **Metadata DB (PostgreSQL)**:
			- `orgs(id, name, plan, created_at)`
			- `users(id, org_id, email, auth_provider, role, quota_bytes, created_at, deleted_at)`
			- `devices(id, user_id, platform, last_seen_at, public_key)`
			- `nodes(id, zone, capacity_bytes, status, last_health_at)` – mirrors CloudSim nodes for scheduling metadata.
			- `files(id, org_id, parent_id, name, mime_type, size_bytes, checksum, is_folder, created_by, created_at, updated_at)`
			- `file_versions(id, file_id, manifest_id, size_bytes, version_number, created_by, created_at, retention_tier)`
			- `shares(id, file_id, principal_type, principal_id, permission, expires_at, created_by)`
			- `audit_events(id, org_id, actor_id, action, target_type, target_id, metadata_json, created_at)`
		- **Object/manifest store**:
			- `manifests/manifest_id.json` containing ordered segments `{node_id, file_id, offset, length, checksum}` to map logical versions to CloudSim chunks.
			- `chunks/{node_id}/{file_id}/{chunk_id}` stored on the corresponding StorageVirtualNode disks; optionally mirrored into an external blob store for backup.
		- **Caching layers**:
			- Redis/KeyDB for session tokens, presigned URL metadata, and hot directory listings (LRU eviction, TTL 5–30 min).
			- CDN/edge cache for frequently downloaded file versions.
		- **Schema evolution**:
			1. Adopt migration tool (Alembic). Each change captured as `version_id`, `down_revision`, `upgrade`, `downgrade` scripts.
			2. Maintain ERDs (dbdiagram) showing relationships (users↔orgs, files self-referencing parent, file_versions referencing manifests).
			3. Enforce foreign keys + cascade deletes to trash tables; add audit triggers for regulatory logging.
	- Prompt: “Map CloudSim entities (nodes, routes, manifests) onto logical services (metadata service, chunk router, replica manager) and identify boundaries for future microservices.”
	- Response:
		- **Metadata Service** ↔ `file_manifests`, controller locate APIs; wraps CloudSim `get_file_manifest` and persists logical file tree in PostgreSQL. Provides gRPC endpoints for CRUD and manifest retrieval.
		- **Chunk Router** ↔ `StorageVirtualNetwork.initiate_file_transfer`, `_select_ingest_target`, manifest registration. Exposed as a service that receives upload session events, decides segment placement, and orchestrates transfers via controller APIs.
		- **Replica Manager** ↔ `_replicate_across_cluster`, `_maybe_expand_cluster`, scaling config. Runs as background worker subscribing to telemetry events to trigger replica seeding or healing workflows.
		- **Health/Telemetry Service** ↔ `node_telemetry`, `_emit_event`. Aggregates metrics, exposes NodeHealth API, and feeds observability stack.
		- **Future microservice boundaries**:
			1. **AuthZ/Sharing** (stateless, purely metadata DB + cache).
			2. **Activity & Notification** (consumes `_emit_event` stream, writes audit events, publishes WebSocket notifications).
			3. **Compliance Vault** (long-term retention + eDiscovery built atop manifest snapshots).
		- Each service communicates via message bus topics: `ingest.requests`, `replica.commands`, `telemetry.events`. CloudSim remains the underlying simulation engine but is wrapped with service contracts, enabling gradual replacement with real infrastructure.
	- Prompt: “Document deployment topologies (single-region dev, multi-region prod) and networking/security assumptions (VPCs, subnets, firewalling, service mesh).”
	- Response:
		- **Dev/Sandbox (single region)**:
			- One VPC (10.10.0.0/16) with public/bastion subnet and private app subnet. CloudSim simulator, metadata DB, and supporting services run on a small Kubernetes cluster or docker-compose stack. Access via VPN + SSO. Simplified network policies allow east-west debugging while still enforcing namespace isolation.
		- **Staging (regional active-passive)**:
			- Two VPCs (10.20.0.0/16, 10.30.0.0/16) per region with peering. Managed Postgres with cross-region replicas, CloudSim nodes deployed as StatefulSets across AZs. Service mesh (Linkerd/Istio) handles mTLS between services and injects tracing headers.
		- **Production (multi-region active-active)**:
			- Regional VPC per geography, connected via Transit Gateway. Each region hosts its own CloudSim cluster (or real storage nodes) plus stateless services behind regional API gateways. Global load balancer (Anycast/GeoDNS) directs clients. Control plane metadata uses multi-primary database (CockroachDB) or primary + async read replicas with conflict resolution.
			- Dedicated subnets: ingress (API gateways, bastions), app (microservices), data (DBs, message brokers), storage (CloudSim nodes). Security groups enforce least privilege; Network ACLs block lateral movement.
		- **Security**:
			- Service mesh enforces mTLS with SPIFFE IDs. API gateways require OAuth scopes. Bastion + privileged services behind Just-In-Time access.
			- WAF + DDoS protection front the global load balancer. Secrets managed via HashiCorp Vault/KMS; nodes retrieve credentials via short-lived tokens.
		- **Operations**:
			- Observability stack deployed per region with centralized aggregation. Disaster recovery drills simulate region loss, validating DNS failover and CloudSim replica promotion.

2. **Control Plane Services**
	- Prompt: “Design the REST/gRPC APIs for uploads/downloads/listing/sharing: include request/response bodies, pagination, filtering, long-running operation hooks, and error contracts.”
	- Response:
		- **REST endpoints (JSON)**:
			- `POST /v1/files` (create folder/metadata) → body `{name,parent_id,is_folder,mime_type}`. Returns `201` + file resource.
			- `POST /v1/uploads:sessions` → `{file_id?,parent_id?,size_bytes,md5}` → returns `{session_id, chunk_size, upload_urls[]}`.
			- `PATCH /v1/uploads/{session}:commit` → finalize chunk ingestion; returns long-running operation (LRO) `{operation_id,status,progress}`.
			- `GET /v1/files/{id}` + `GET /v1/files/{id}/download` (optionally `Accept: application/octet-stream`).
			- `GET /v1/files?page_size,page_token,filter=parent_id:123` for listing; `sort=updated_at desc`.
			- `POST /v1/files/{id}:share` with `{principals:[...],permission,expires_at}`; returns share objects.
			- Standard error envelope: `{error:{code,string,message,details:[]}}` with HTTP semantics (`400` validation, `401` auth, `403` ACL, `404` missing, `409` conflict, `429` throttled, `500` internal, `503` busy).
		- **gRPC services** mirror REST for internal use with streaming for uploads/downloads; define protobuf messages for `File`, `Version`, `Share`, `ListFilesRequest/Response`, `DownloadChunk` stream.
		- **LROs**: Expose `GET /v1/operations/{id}` + `Cancel` to poll ingestion/replication jobs. Each includes `metadata` with chunk counts and manifest IDs.
		- **Pagination/filtering**: Use opaque `page_token` (base64 cursor). Filters follow FIQL-like syntax (`mime_type:image/* AND owner=user123`). gRPC uses repeated filter fields.
	- Prompt: “Specify upload session lifecycle (initiate, chunk append, finalize, abort) and how it drives CloudSim ingestion jobs.”
	- Response:
		1. **Initiate**: Client requests session; Upload Orchestrator allocates `session_id`, determines chunk size (2–8 MB) based on network heuristics, reserves manifest entry, and records expected checksum. Returns presigned URLs (gateway proxies to chunk router) and expiry timestamps.
		2. **Chunk append**: Client PUTs chunks with headers `Content-Range`, `Chunk-Id`, `Session-Id`. Gateway validates token, streams chunk into CloudSim via `initiate_file_transfer` using `segment_offset`. Successful chunk writes emit events captured for progress tracking.
		3. **Heartbeat/progress**: Client polls `GET /uploads/{session}`; service aggregates chunk completion from CloudSim events, exposing `received_bytes` and `pending_nodes`.
		4. **Finalize**: Client sends `PATCH ...:commit` with final checksum + optional metadata. Orchestrator verifies all chunks complete, merges manifest segments, writes `file_versions` row, and enqueues replication job. Returns LRO that completes when replication minimum satisfied.
		5. **Abort/timeout**: Client may `DELETE /uploads/{session}`; orchestrator cancels outstanding transfers, instructs CloudSim to `abort_transfer`, releases reserved storage, and logs audit event. Sessions also auto-expire after N hours via cleanup worker.
	- Prompt: “Detail authentication & authorization strategy (OIDC login, refresh/session tokens, service-to-service mTLS) and map ACL evaluation to controller calls.”
	- Response:
		- **Identity**: External IdP (Auth0/Azure AD) using OIDC. Users authenticate via PKCE; obtain ID token + refresh token. Short-lived access tokens (15 min) stored in browser memory; refresh via silent flow.
		- **Session service**: Issues session cookies (HttpOnly, SameSite=Lax) for web; mobile/CLI store bearer tokens. Tokens contain org/user IDs, roles, device fingerprint, and scopes.
		- **Service-to-service**: Mesh-issued mTLS certs (SPIFFE). Each microservice enforces mutual auth and checks SPIFFE ID against RBAC policy.
		- **Authorization**: Central Policy Engine (OPA/cedar) evaluates ACLs. Inputs: user claims, file metadata, share entries, org policies. Decisions cached in Redis with 60s TTL.
		- **Controller integration**: Every mutating call to CloudSim controller includes `request_context` describing actor/principal and resolved permissions. Controller enforces coarse checks (e.g., node admin actions) while metadata service ensures fine-grained file permissions before scheduling ingestion/pull operations.
	- Prompt: “Define background schedulers/workers for replication, cleanup, audit fan-out, and quota enforcement, including their queues/messages.”
	- Response:
		- **Message bus**: Kafka/NATS topics with dead-letter queues. Common envelope: `{event_id, type, payload, retries}`.
		- **Replication worker**: Consumes `replication.requests` (generated post-upload). Determines target nodes per policy, invokes CloudSim `initiate_replica_transfer`, tracks LRO completion, and updates manifest statuses. Retries with exponential backoff; escalates to Ops if target capacity unavailable.
		- **Cleanup worker**: Subscribes to `uploads.expired` and `trash.expired`. Calls controller to abort stale transfers, deletes orphan manifests, and frees reserved storage. Runs periodic scans as cron job.
		- **Audit fan-out**: Listens to `_emit_event` stream mirrored into `activity.events`. Normalizes into audit rows and pushes notifications to WebSocket hubs / email queue.
		- **Quota enforcer**: Nightly job aggregates usage per org/user from metadata DB + CloudSim telemetry, compares against plan limits, and enqueues `quota.alert` events. Can temporarily block new uploads by toggling feature flags in control plane.
		- **Scheduler infra**: Use temporal.io or Celery beat for cron semantics; jobs idempotent and instrumented with tracing.

3. **Storage & Replication Logic**
	- Prompt: “Formalize replica placement policies: number of replicas per tier, cross-zone rules, hot/cold storage transitions, spillover chunking thresholds, and rebuild workflows when capacity shrinks.”
	- Response:
		- **Replica counts**: Default 3 replicas (2 hot, 1 cold). Enterprise tier supports 4+ with geo-distributed copy. Small plans can opt for 2 with erasure coding supplement.
		- **Cross-zone rules**: Hot replicas must reside in distinct availability zones within same region; cold replica placed in secondary region. Placement algorithm uses weighted round-robin factoring free capacity, latency, and failure domains tracked by CloudSim clusters.
		- **Spillover thresholds**: Files >50 MB automatically segmented; chunk size dynamic (8–32 MB). When node free capacity <10%, `_select_ingest_target` excludes node to prevent overfill. Spillover ensures contiguous offsets recorded in manifest.
		- **Hot→cold transitions**: Lifecycle policy monitors access frequency. After 30 days idle, demote to cold (archive nodes) but keep one hot replica for fast recall. Transitions trigger `replication.requests` jobs to move segments accordingly.
		- **Capacity shrink/rebuild**: When node removed or falls below health SLA, Replica Manager seeds replacements before decommission. Workflow: mark node draining → replicate missing datasets → update manifests → instruct CloudSim to free old copies → update metadata DB statuses. All steps idempotent and logged.
	- Prompt: “Describe consistency guarantees (eventual vs. strong) for metadata vs. file content and outline conflict resolution for concurrent edits.”
	- Response:
		- **Metadata**: Strongly consistent within a region via serializable transactions (Postgres/Cockroach). Cross-region replication achieves bounded-staleness (≤3s) using Raft; writes use leader/follower pattern.
		- **File content**: Eventual consistency—manifests update after chunk replication completes. Clients receive download tokens only after minimum replica quorum achieved, ensuring read-after-write for owners.
		- **Concurrent edits**: Uses optimistic concurrency with version preconditions (`if-match` header). When conflicts occur, server creates new version and marks previous as ancestor, surfacing “conflict copy” to user. Activity log records both operations. Collaborative editors can leverage operational transforms stored per document type, but binary files rely on last-writer-wins with notification.
		- **Locking**: Optional advisory locks for office documents; stored in Redis with lease extension. On lock expiration, next writer acquires lock and triggers merge workflow.
	- Prompt: “Design background healing: checksum scans, manifest reconciliation, orphan chunk garbage collection, degraded node evacuation.”
	- Response:
		- **Checksum scans**: Each node schedules rolling scrubs (daily for hot, weekly for cold). Worker reads chunks, validates checksum vs. manifest, and reports discrepancies to Healing Queue.
		- **Manifest reconciliation**: Periodic job compares metadata DB manifests against CloudSim `segment_manifests`. Missing segments trigger rehydration (copy from healthy replica) or mark version degraded. Extra segments without metadata become orphans queued for GC after retention window.
		- **Garbage collection**: GC worker consumes `gc.candidates`, verifies no references exist (metadata + pending operations), then instructs CloudSim node to delete file IDs and releases disk space. Rate-limited to avoid IO spikes.
		- **Degraded node evacuation**: When telemetry signals high failure rate or impending maintenance, Replica Manager places node in `draining` state, issues replication jobs for all manifests primarily stored there, and updates routing tables. Once empty, node can be rebooted or decommissioned safely.
	- Prompt: “Specify durability strategies: erasure coding vs. full copies, snapshot cadence, encryption-at-rest/in-transit plans, key management.”
	- Response:
		- **Replication vs. erasure coding**: Files <256 MB use triple replication for simplicity. Larger assets optionally encoded with Reed-Solomon (k=8, m=4) stored across 4+ nodes; CloudSim nodes track stripe membership in manifest metadata.
		- **Snapshots/backups**: Metadata DB snapshotted hourly and shipped to cold storage; CloudSim manifests exported daily. Object storage/backups maintain 30-day retention with weekly full + daily incremental.
		- **Encryption**: In transit via TLS 1.3/mTLS. At rest, per-chunk envelope encryption using AES-256-GCM. Keys derived from customer master keys (CMKs) stored in cloud KMS; DEKs rotated every 24h or per upload if high sensitivity.
		- **Key management**: Hierarchical—tenant-level CMK, service-level intermediate key, per-file DEK. KMS audit logs tracked for compliance. Support for customer-managed keys (bring-your-own) where controller fetches wrapping key under least privilege.

4. **User Experience**
	- Prompt: “Draft UX workflows for web/mobile/CLI covering onboarding, upload, folder organization, sharing, commenting, activity feed, offline sync cues; include wireframes or state charts.”
	- Response:
		- **Web**:
			1. **Onboarding**: SSO/OIDC flow → profile completion (preferred language, storage tier hints) → guided tour overlay that highlights upload button, activity feed, and sharing sidebar. Progress stored in `user_preferences.completed_tours` so the tour only replays after major releases.
			2. **Uploads**: User hits global “New” → chooses upload/folder template → drag/drop initiates resumable session. Progress tray shows per-file states (hashing, uploading, replica fan-out) and links to background job IDs for support. Errors surface inline with retry CTA.
			3. **Organization**: Tree-and-grid hybrid. Breadcrumb keeps parent context; left nav lists favorites and shared-with-me filters. Bulk actions (move, label, pin) operate on virtual selections so pagination doesn’t break multi-select.
			4. **Sharing/commenting**: Side panel toggles between People and Activity tabs. Sharing dialog handles users/groups/public links, exposing permission presets and expiry calendar. Comment threads attach to manifest versions; mentions trigger notifications.
			5. **Activity & offline**: Activity feed streams live events (upload complete, comment added, share created). Offline banner appears when websocket drops; UI queues actions (rename, comment) and replays once connectivity returns, matching chunk engine queued operations.
		- **Mobile**:
			1. **Onboarding**: Lightweight screens (sign-in, permissions for camera/storage, choose sync scope). Device registration API ties push token + platform for notifications.
			2. **Uploads**: Native picker sends files/photos into background service; shows per-item pill with pause/resume. Queue respects battery/network heuristics (Wi-Fi-only toggle) and uses the same resumable API endpoints.
			3. **Folders/sharing**: Tabbed layout (Browse, Shared, Recent, Offline). Long-press reveals quick actions. Sharing/comms slide-up sheet reuses REST APIs; comments display as chat-style timeline with read receipts.
			4. **Activity/offline cues**: Push notifications deep-link into item detail. Offline tab lists files marked for sync along with last refresh timestamp; manual refresh triggers delta sync via `/activity` since cursor.
		- **CLI/Desktop sync**:
			1. **Onboarding**: `nexus drive login` opens device code flow. Config wizard writes `~/.nexusdrive/config` (org, default workspace, bandwidth cap).
			2. **Upload/download**: Commands (`upload`, `sync`, `watch`) stream to chunk engine via gRPC. CLI prints progress bars with chunk/sec, retries, and replica quorum state.
			3. **Organization/sharing**: `drive ls`, `drive mv`, `drive share --principal user@example.com --role editor`. Comments posted via `drive comment add --file FILE_ID` hooking into Activity Service APIs.
			4. **Offline**: Desktop sync agent tracks file graph locally (SQLite) and surfaces tray status (Last synced, conflicts). Conflicts prompt comparison view linking back to web diff UI.
	- Prompt: “Define resumable upload/download flows (chunk size negotiation, retry logic, parallel streams) and how they map to the existing chunk engine APIs.”
	- Response:
		1. **Negotiation**: Clients call `POST /uploads:sessions` with hints (network type, device class). Upload Orchestrator calculates chunk size using heuristics (base 8 MB, drop to 2 MB on mobile, raise to 32 MB on LAN) and embeds it plus a `max_parallel_streams` field in the session.
		2. **Chunk append**: Each PUT/POST to `/uploads:chunk` or gRPC `AppendChunk` includes `Content-Range`, `Chunk-Id`, and checksum. Server validates size, forwards to `StorageVirtualNetwork.initiate_file_transfer`, and acknowledges only after the chunk is persisted on the ingress node.
		3. **Retries**: Sessions store per-chunk state machine (Pending → InFlight → Committed). Clients retry idempotently using `Chunk-Id`; Upload Orchestrator discards duplicates by comparing offsets + checksum, keeping metrics for throttling abusive clients.
		4. **Parallelism**: Clients may upload up to `max_parallel_streams` concurrent chunks. Controller pipelines them through VirtualOS so disk/network limits throttle automatically. For downloads, `GET /files/{id}/download?chunk_size=N` or the gRPC stream supports multi-range/resume via `Range` headers and operation tokens.
		5. **Failure handling**: Heartbeat endpoint `/uploads/{session}` returns gap map; if session idle longer than policy (default 4 h) cleanup worker queues it onto `uploads.expired`. Desktop/mobile agents observe websocket `upload.session.expiring` events to resume before timeout.
		6. **Mapping to chunk engine APIs**: Upload Orchestrator only ever calls `UploadOrchestrator.append_chunk` and `finalize_upload`, which in turn call `StorageVirtualNetwork.initiate_file_transfer` + `CloudSimController.resume_transfer`. Parallel reads use `api_gateway.stream_download`, which streams manifests -> chunk bytes via `UploadService.stream_file` (shared with CLI). This ensures one transport path regardless of client form factor.
	- Prompt: “Describe notification & activity surfaces (real-time toasts, email summaries, audit timeline) and how they subscribe to controller events.”
	- Response:
		1. **Event sources**: Activity Service subscribes to `activity.events`, `replication.requests`, `healing.events`, and `lifecycle.transitions`. It normalizes payloads into `activity_feed` rows with `scope` (user/org/file) and `severity`.
		2. **Real-time toasts**: Web/mobile clients maintain a websocket (`/activity/stream?cursor=`). Server fan-out uses Redis pub/sub or NATS JetStream to push events filtered by ACL. Toasts cover upload completion, comment mentions, share changes, lifecycle moves, and quota alerts. Each toast links to detail panels.
		3. **Email summaries**: Daily digest job aggregates unread activity per user (with quiet hours). Templates include sections for “Shared with you”, “Comments & Mentions”, “Approvals/Requests”. Links carry deep-link tokens; unsubscribes map to notification preferences stored in metadata DB.
		4. **Audit timeline**: Admin dashboard queries the same activity store but exposes filters (actor, action, file, IP). Timeline visualizes parallel tracks (User actions vs. System events). Export pipeline writes CSV/JSON to compliance vault on demand.
		5. **Mobile/CLI hooks**: Push notifications triggered via SNS/FCM when Activity Service marks an event as high priority (mentions, transfer failures). CLI polls `/activity?cursor=`; for long-running operations it subscribes to SSE to mirror toaster updates in terminal.
		6. **Reliability**: Clients ack websocket batches; on reconnect they resume via cursor. If cursor expired, server replays from durable store (14-day retention) or instructs client to fetch archived audit logs.
	- Prompt: “Plan accessibility/internationalization requirements (keyboard navigation, localization, right-to-left).”
	- Response:
		- **Accessibility**:
			1. WCAG 2.1 AA compliance: color contrast ≥4.5:1, focus outlines, reduced motion option, and captions for video previews.
			2. Full keyboard support: tab/shift-tab order mirrors DOM hierarchy; multi-select lists use arrow keys + space. Shortcut palette (`Ctrl+/`) lists bindings and respects OS conventions.
			3. Screen readers: ARIA landmarks for navigation/content regions, live regions for toast notifications, descriptive labels for buttons (e.g., “Share file example.pdf”).
			4. High-density & reduced motion modes toggled via profile settings or prefers-reduced-motion media query.
		- **Internationalization**:
			1. Locale packs stored in Fluent/ICU message catalogs; build pipelines extract strings automatically. Default languages: en, es, fr, de, ja, pt-BR; roadmap adds RTL languages (ar, he).
			2. Date/number formatting uses Intl APIs server- and client-side. Storage sizes localized with binary prefixes and localized decimals.
			3. Right-to-left: layout components support `dir=rtl`, flipping icons/chevrons automatically. Canvas-based previews read UI dir from context to avoid mirrored text.
			4. Input methods: ensure IME composition supported in rename/search fields; mobile apps respect system fonts and dynamic type sizes.
			5. Localization workflow: Phrase/TMS integration with screenshot context, pseudo-localization gate in CI to catch truncation, and per-release linguistic QA.

5. **Collaboration & Metadata Features**
	- Prompt: “Specify versioning semantics (auto-save generations, labels, pinning, restore UI) and how manifests reference historical chunks.”
	- Response:
		1. **Version model**: Every edit produces a `file_versions` row with monotonic `version_number`, `parent_version_id`, and `change_summary`. Auto-save creates lightweight “generation” rows flagged as `autosave=true`; labeling/pinning flips boolean columns (`is_pinned`, `label` FK) so UI can highlight important revisions without duplicating blobs.
		2. **Manifest linkage**: Each version references a manifest ID; manifests store `source_version_id` for provenance. When delta uploads reuse chunks, manifests point to the same `chunk_id` across versions so dedup stats remain accurate.
		3. **Restore UI**: Web timeline lists versions with diff badges (edited, commented, shared). Restoring clones manifest + metadata into new head version while marking previous head as ancestor. CLI supports `drive versions list/restore` commands.
		4. **Concurrency hints**: Clients fetch current `version_etag`; updates include `If-Match`. On mismatch, UI surfaces conflict modal with side-by-side diff (metadata vs. content) and offers duplicate copy or merge instructions.
		5. **Retention hooks**: Policies per org/workspace set min/max versions retained; archival workflow exports older manifests to cold object store but keeps metadata pointers for legal hold searches.
	- Prompt: “Define trash/restore lifecycle (soft delete windows, hard delete jobs, retention policies per workspace).”
	- Response:
		1. **Soft delete**: `files` rows keep `deleted_at` + `deleted_by`. When flagged, entries move to `trash` view but remain in primary tree for policy enforcement. Default retention: 30 days (configurable per workspace/plan).
		2. **Restore**: User restores via UI/CLI; system revalidates parent folder availability, rehydrates manifests if cold-tiered, and clears `deleted_at`. Restores emit activity + audit events.
		3. **Hard delete**: `trash.expired` worker scans for `deleted_at < now - retention` and enqueues GC job. GC verifies no legal hold, removes share rows, versions, manifests, and instructs CloudSim to delete chunks via `collect_orphans` flow. Audit log stores irreversible tombstone record.
		4. **Retention overrides**: Admins can extend retention per org/folder; legal hold flag halts GC regardless of retention window. System surfaces countdown banners to owners and emails summary before purge.
	- Prompt: “Outline sharing models (user/group/public links, permission levels, expiration, password-protected links) and enforcement points.”
	- Response:
		1. **Permission matrix**: Roles map to capabilities—Viewer (read/download), Commenter (read + comment), Editor (upload/edit/delete), Manager (share/restore). ACL table stores `(principal_type, principal_id, permission, expires_at, password_hash?)`.
		2. **User & group sharing**: Groups resolve via directory sync; ACL evaluation expands groups into members at runtime with caching. Changes propagate via activity feed and optional email.
		3. **Public links**: Generate opaque token stored in `shares` row with `link_token`, optional `password_salt/hash`, download caps, and expiry. Links serve via gateway endpoint that enforces password challenge + rate limiting before proxying download stream.
		4. **Enforcement points**: API gateway enforces request-scoped ACL decision before hitting Metadata Service. Chunk downloads require signed URL minted only when ACL passes. CLI/mobile embed signed URL validity (≤5 min) to limit token reuse.
		5. **Auditing**: Every share create/update/delete logs actor, principal, permission, and source IP. Public link downloads log viewer fingerprint for anomaly detection; suspicious access triggers forced password reset for that link.
	- Prompt: “Plan search indexing pipelines (metadata index, content OCR/preview extraction, incremental updates) plus activity/audit logging schemas and retention.”
	- Response:
		1. **Metadata index**: Change data capture stream (Debezium) feeds Elasticsearch/OpenSearch. Indexed fields: names, labels, owners, MIME, tags, share principals, and custom metadata. Updates keyed by `file_id` + `version_id` for point-in-time queries.
		2. **Content enrichment**: Ingestion worker pulls new versions, runs OCR (Tesseract/Azure Vision), text extraction (Apache Tika), and thumbnail generation. Extracts stored in `content_artifacts` bucket and referenced in search index for full-text queries and preview cards.
		3. **Incremental pipeline**: `activity.events` publishes `file.updated`/`share.changed`; search worker consumes, fetches latest manifest, and reindexes selectively. Backfill job handles reindex of entire org when schema changes.
		4. **Activity/audit schema**: `activity_feed(id, org_id, file_id, actor_id, event_type, payload_json, created_at, severity)` for user-facing stream. `audit_events` (already defined) stores immutable compliance trail with retention default 7 years (configurable). Cold storage tier archives events past 2 years into affordable object storage but accessible via on-demand query.
		5. **Privacy controls**: Data minimization ensures OCR text honors DLP rules; sensitive classifications label index entries so search results obey clearance levels. Users can request purge of personal data; workflows scrub both metadata index and content artifacts.

6. **Operations & Observability**
	- Prompt: “List dashboards/alerts: ingest success rate, latency distributions, storage utilization, replica health, queue depth, auth failures, API saturation.”
	- Response:
		1. **Dashboards**:
			- **Ingest & API**: p95/p99 latency, request volume, error budget burn, upload session state (active, stalled, failed), `run_background_jobs` cadence. Breakdowns by endpoint (REST/gRPC) and client type.
			- **Storage/Replica Health**: Node utilization heatmaps (CPU, RAM, disk), replica quorum compliance, lifecycle transitions count, durability policy actions (erasure coding, checksum repairs).
			- **Queue & Worker**: Kafka/NATS topic depth, consumer lag, worker success/failure rates, retry distribution, DLQ size.
			- **Security/Auth**: Auth success/failure by reason, rate-limit hits, WAF blocks, mTLS handshake errors.
			- **User Experience**: Frontend CLS/TTFB, offline incidence, comment/share activity, search latency.
		2. **Alerts**:
			- Ingest success rate drops below 99.5% over 15 min.
			- Upload/gRPC latency above SLO (p95 > 2 s) for 10 min.
			- Storage node utilization > 85% sustained for 30 min or disk fill < 5% free.
			- Replica quorum violations > 10 active manifests.
			- Queue lag > 5 min or DLQ growth > 100 msgs.
			- Auth failures spike (3× baseline) or WAF blocks > threshold, triggering security incident workflow.
	- Prompt: “Define SLOs/SLAs and tie them to alert thresholds and on-call escalation policies.”
	- Response:
		1. **Core SLOs**:
			- Upload finalize success ≥ 99.9% per rolling 30 days.
			- Download p95 latency ≤ 1.5 s for 50 MB files.
			- Metadata API availability ≥ 99.95% monthly.
			- Background repair completion within 15 min of node failure detection for 99% of manifests.
		2. **SLAs**:
			- Enterprise plan: 99.9% monthly availability, support response in 1 h, data durability 11 nines.
			- Business plan: 99.5% availability, 4 h response.
		3. **Alert tiers**:
			- **Warning**: 50% error budget burn or latency trending. Notify on-call via Slack, investigate within 30 min.
			- **Critical**: SLO violation imminent (e.g., 85% burn) or actual SLA breach; page on-call stack (primary → secondary → manager). Incident runbook includes impact assessment, mitigation steps, comms template.
		4. **Escalation**: PagerDuty schedules mapped to regions. Tiered escalation: SRE → duty manager → exec sponsor. Incidents logged in Ops portal with blameless postmortems due within 48 h.
	- Prompt: “Detail backup/restore/disaster-recovery procedures: snapshot cadence, cross-region replication, failover drills, RPO/RTO targets, runbooks.”
	- Response:
		1. **Backups**:
			- Metadata DB: point-in-time backups every 5 min, hourly snapshots retained 30 days, weekly full retained 1 year.
			- Manifest/object store: daily incremental, weekly full exported to cold tier (Glacier/Archive) with integrity checksums.
			- Activity/audit logs: streamed to immutable storage (WORM) with 7-year retention.
		2. **DR Strategy**:
			- Active-active control plane with async replication; failover via DNS + feature flags.
			- Storage fabric replicates manifest + data across at least two regions. RPO 5 min (metadata), RTO 30 min for regional failover.
		3. **Runbooks**:
			- Backup restore steps documented for DB, manifests, audit store. Include validation queries, checksum verification, and reindex instructions.
			- Disaster drill schedule quarterly: simulate regional outage, run failover playbook, capture metrics vs. RPO/RTO.
			- Post-drill review logs gaps and update automation (Terraform/Ansible) to codify manual steps.
	- Prompt: “Describe capacity planning models (growth projections, cost models, auto-scaling triggers) and tooling to forecast node additions.”
	- Response:
		1. **Forecasting**:
			- Blend historical usage (weekly uploads, active users, average file size) with sales pipeline forecasts. Model storage growth per tier (hot/cold) and compute/bandwidth needs.
			- Use Prophet/ARIMA models fed by telemetry warehouse (BigQuery/Snowflake) to project 3, 6, 12 months. Include seasonality (end-of-quarter spikes) and new feature launches.
		2. **Cost modeling**:
			- Maintain BOM per node (compute, storage, licensing). Dashboard compares actual vs. forecasted spend. Identify break-even for erasure coding vs. replication.
		3. **Auto-scaling**:
			- Kubernetes HPA for stateless services based on CPU + queue lag.
			- CloudSim nodes scale via controller API triggered when utilization > 70% or replica queue backlog > threshold. Pre-provision spare capacity per region to hit RTO.
			- Message brokers scale partitions/consumers via Terraform modules once utilization > 75%.
		4. **Tooling**:
			- Capacity planner CLI pulls telemetry snapshots, runs simulations (e.g., node failure + growth) to recommend node additions.
			- Reports exported monthly for finance + ops review, highlighting risks (e.g., hot tier exhaustion in 45 days) with recommended actions.

7. **Testing & Rollout**
	- Prompt: “Create the testing matrix: unit coverage goals, integration suites (API + chunk router), load tests (multi-GB uploads, 1000s of concurrent clients), chaos experiments (node/link failure, corruption).”
	- Prompt: “Plan staging environments mirroring production topology, with synthetic data seeding and replay harnesses.”
	- Prompt: “Outline CI/CD pipeline: linting, tests, canary deploys, feature flags, rollback triggers, database migration safety.”
	- Prompt: “Define release management: versioning, change management approvals, customer communication cadence, beta programs.”
