## Cloud Storage Build-Out Prompt TODO

1. **Foundation & Architecture**
	- Prompt: “Describe the end-to-end architecture for a Google Drive–style system using CloudSim’s virtual infrastructure. Include services, data flow, and component boundaries.”
	- Prompt: “Define the data models (users, files, versions, shares, nodes) and persistence choices (DB schema + storage layout).”

2. **Control Plane Services**
	- Prompt: “Design the REST/gRPC API for file uploads, downloads, listing, sharing, and metadata updates.”
	- Prompt: “Detail authentication/authorization strategy (sessions, API tokens, ACLs) and how it integrates with the controller.”

3. **Storage & Replication Logic**
	- Prompt: “Specify policies for replica placement, spillover chunking, and background healing across zones.”
	- Prompt: “Outline data integrity workflows (checksums, verification jobs, recovery).”

4. **User Experience**
	- Prompt: “Draft UX workflows (web UI + CLI) for uploading, organizing folders, sharing links, and viewing activity.”
	- Prompt: “Describe resumable upload/download flows and how they map onto the existing chunk engine.”

5. **Collaboration & Metadata Features**
	- Prompt: “Define file versioning, trash/restore, and sharing permissions logic.”
	- Prompt: “Plan search indexing and activity/audit logging pipelines.”

6. **Operations & Observability**
	- Prompt: “List operational dashboards, alerts, and metrics required for the service.”
	- Prompt: “Detail backup/restore, disaster recovery, and capacity planning procedures.”

7. **Testing & Rollout**
	- Prompt: “Create a comprehensive testing matrix (unit, integration, load, chaos) for the cloud storage platform.”
	- Prompt: “Outline deployment pipeline, staging environments, and rollout/rollback strategy.”
