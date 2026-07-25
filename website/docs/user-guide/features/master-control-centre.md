---
sidebar_position: 16
title: "Master Control Centre"
description: "Read-only dashboard cockpit for product configuration, Kanban attention, agent activity, and Agent Factory evidence"
---

# Master Control Centre

The Master Control Centre is an authenticated, read-only tab in `hermes dashboard`. It brings urgent Kanban work, configured products, agent activity, Agent Factory evidence, and source health into one monitor-and-navigate view.

It does not replace Kanban, Profiles, Sessions, Cron, Chat, or Agent Factory. Those remain authoritative. The Control Centre reads them and links back to their existing pages; it has no mutation buttons.

## Open it

Start the dashboard and select **Control Centre** in the sidebar:

```bash
hermes dashboard
```

The local dashboard opens at `http://127.0.0.1:9119/control-centre` by default.

## Configure products

Create `$HERMES_HOME/control-centre/products.yaml` (`~/.hermes/control-centre/products.yaml` for the default profile):

```yaml
apiVersion: control-centre/v1
products:
  - id: example
    name: Example Product
    configuration_state: configured
    repositories:
      - id: app
        path: /workspace/example
        remote: origin
        default_branch: main
    kanban:
      board: default
      tenant: example
    knowledge_roots:
      - /workspace/vault/example
    environments:
      - name: staging
        production: false
      - name: production
        production: true
```

Identifiers must be lowercase letters, numbers, and hyphens. Paths must be absolute and inside an allowed workspace/home root. Use `configuration_state: incomplete` when a product is known but not fully configured; the dashboard shows the gap explicitly.

The registry rejects unknown or secret-shaped fields. One invalid product produces a visible source error without hiding valid sibling products.

## What the page shows

- **Attention** — blocked, review-required, running, stale-worker, and diagnostic items from Kanban.
- **Active Builds** — manifest-bound Agent Factory evidence and recorded `PASS`, `FAIL`, `SKIPPED`, or `UNKNOWN` layer verdicts; a missing or invalid manifest fails closed to `FAIL`.
- **Agent Fleet** — profile, role, current task/run, heartbeat, lease, model/provider, skill count, and diagnostics where available.
- **Products** — configuration state, repository identifiers/remotes/default branches, environments, and links to Kanban, Chat, and Sessions.
- **System** — snapshot schema, staleness threshold, source-error count, and links to Cron, Logs, System, and Sessions.

Use the product, agent, severity, and freshness filters to narrow the snapshot. Filters remain active when switching sections.

## Freshness and partial failures

Every displayed record names its source, source identifier, observation time, and freshness:

- `live` — read successfully from the authority;
- `cached` — supplied by a source cache;
- `stale` — older data retained after age or refresh failure;
- `unavailable` — the source or record could not be read safely.

The process-local snapshot cache lasts five seconds. A broken source does not blank the whole page: healthy products, agents, attention, and builds remain visible, while the failed source gets an error and recovery message. A dashboard restart rebuilds the snapshot and does not change Kanban or any other source.

## Deliberately hidden data

The browser payload removes repository filesystem paths, knowledge-root paths, and agent workspace paths. It also excludes secrets, environment values, session messages, cron prompts/output, Kanban comments, and raw release files. Product remotes are displayed as labels, not trusted external links.

## Remote dashboard and Desktop

Localhost is the default. To access the dashboard remotely, configure an authenticated dashboard and bind it to a reachable interface. Do not expose an unauthenticated dashboard to a network. See [Web Dashboard](./web-dashboard#connecting-hermes-desktop-to-a-remote-backend) for the supported auth and Desktop connection flow.

Hermes Desktop can attach to the same remote dashboard backend. The Phase 8 MVP does **not** add a native Desktop Control Centre page; use the web dashboard tab for this view.

## Troubleshooting

| Problem | What to do |
|---|---|
| **No products configured** | Create `$HERMES_HOME/control-centre/products.yaml` with `apiVersion: control-centre/v1`. |
| **Invalid registry warning** | Fix the exact YAML/product error shown. Valid sibling products remain available. |
| **Source unavailable** | Follow the recovery text and open the linked canonical Hermes page. |
| **Stale worker** | Inspect its Kanban task, run history, heartbeat, and lease. The Control Centre will not reclaim it. |
| **Session expired** | Sign in to the dashboard again, then refresh the page. |
| **Dashboard stopped** | Restart `hermes dashboard`. Source data remains intact because the Control Centre stores no operational truth. |
| **Old snapshot after repair** | Wait for the five-second cache window or select **Refresh**. |

## Read-only API

Authenticated clients can read:

- `GET /api/plugins/control-centre/snapshot`
- `GET /api/plugins/control-centre/attention`
- `GET /api/plugins/control-centre/products`
- `GET /api/plugins/control-centre/agents`
- `GET /api/plugins/control-centre/builds`
- `GET /api/plugins/control-centre/health`

No POST, PATCH, PUT, or DELETE route is part of the MVP.

## Not included yet

GitHub PR/check ingestion, vault and memory browsing, skill changes, typed approvals, watchdog remediation, and a native Desktop view are later milestones requiring separate review and approval.
