# CloudSim Web UI

A sleek single-page experience for operating the Nexus CloudSim environment: monitor storage nodes, check transfers, browse files, and watch SLOs without leaving the browser.

## Features

- **Realtime dashboards** – utilization cards, topology view, sparkline SLOs, and live transfer progress.
- **Configurable target** – point the UI to any REST base/token via the Environment panel.
- **Command console** – add/fail/restore nodes, trigger demo uploads, or advance the simulator directly from the browser.
- **Graceful fallbacks** – ships with sample data so the UI looks polished even when APIs are offline.
- **Modern stack** – React + Vite + Tailwind + Recharts for fast iteration and high-fidelity visuals.

## Getting Started

```powershell
cd webui
npm install
npm run dev
```

- The dev server runs on `http://localhost:5173`.
- Use the Environment panel (top right) to set `https://<your-host>` and bearer token matching CloudSim.
- All API calls go through the REST interface; add more endpoints inside `src/lib/api.ts` as the platform grows.

### Connecting to staging

| Field | Example | Notes |
| --- | --- | --- |
| REST base URL | `https://staging.api.nexusai.dev` | Mirrors `STAGING_REST_BASE` from the GitHub Action secrets. |
| Auth token | `Bearer <shared staging token>` | Same token used for `seed_demo_data.py`/`replay_traffic.py`. |

The UI currently reads:

- `GET /v1/storage/nodes`
- `GET /v1/transfers`
- `GET /v1/files?limit=25`
- `GET /v1/activity?limit=10`
- `GET /v1/observability/slo/burn-rate`
- `GET /v1/auth/profile`
- `GET /v1/observability/grafana/panels`

And the **Command Console** now writes to:

- `POST /v1/control/nodes` (provision nodes on demand)
- `POST /v1/control/nodes/{id}:fail`
- `POST /v1/control/nodes/{id}:restore`
- `POST /v1/control/uploads/demo`
- `POST /v1/control/sim/tick`

Add additional handlers by editing `src/lib/api.ts` and surface them through new cards or tables.

## Production Build

```powershell
npm run build
npm run preview # optional smoke test
```

The build step generates static assets under `webui/dist/`. Serve them via any CDN or FastAPI `StaticFiles` mount.

## Next Ideas

1. Enable OAuth or signed links so demos avoid manual token paste.
2. Add deep linking into transfers/files for shareable demo states.
3. Embed additional Grafana panels or OAuth scopes by updating `src/lib/api.ts` and the `GrafanaEmbed` / `OAuthStatusCard` components.
