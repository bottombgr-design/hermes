# Master Control Centre — read-only MVP

Status: implemented for Phase 8 review
Package: `control_centre/`
Dashboard plugin: `plugins/control-centre/dashboard/`
Route: `/control-centre`
API namespace: `/api/plugins/control-centre/`

## Purpose and authority boundary

The Master Control Centre is an authenticated, read-only operator cockpit. It joins product configuration, Kanban attention and worker activity, Hermes profile metadata, and Agent Factory evidence into one disposable snapshot. It is not an orchestration service and it is never an authority for the data it displays.

The implementation adds no model tool and no core dashboard route. A dashboard plugin owns the API and presentation. Existing Hermes pages remain the action surfaces; the Control Centre only deep-links to them.

| Displayed concern | Authoritative source | Adapter behavior |
|---|---|---|
| Products and environments | `$HERMES_HOME/control-centre/products.yaml` | Strict allowlist schema; valid rows survive invalid siblings |
| Tasks, attention, worker activity, leases | Kanban SQLite board through `hermes_cli.kanban_db` | Read-only queries and existing diagnostics |
| Profile identity, model, provider, skill count | Profile discovery through `hermes_cli.profiles.list_profiles()` | Joined to current Kanban activity by profile name |
| Agent Factory build and review evidence | `$HERMES_HOME/agent-factory/releases/<release>/` | Recomputes manifest integrity and checks release/hash bindings |
| Session source health | `$HERMES_HOME/state.db` | Opens `SessionDB` read-only; message bodies never enter the snapshot |
| Cron source health | `$HERMES_HOME/cron/jobs.json` | Parses job metadata only; job prompts and output are absent |

The snapshot is process-local and disposable. Dashboard restart or cache expiry rebuilds it from those sources.

## Product registry

The registry uses `apiVersion: control-centre/v1` and a `products` list. Product and repository identifiers are stable lowercase identifiers (`[a-z0-9][a-z0-9-]{0,62}`). Unknown fields fail closed for that row.

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

Allowed product keys are `id`, `name`, `repositories`, `kanban`, `knowledge_roots`, `environments`, and `configuration_state`. Repository keys are `id`, `path`, `remote`, and `default_branch`; Kanban keys are `board` and `tenant`; environment keys are `name` and boolean `production`.

Repository and knowledge paths must be absolute and resolve under an allowed root. A product can set `configuration_state: incomplete`; the UI then shows recovery guidance instead of inventing missing Helm or repository data. Secret-shaped keys (`token`, `password`, `secret`, `private_key`, and `dsn`, including suffixed forms) are rejected recursively.

## Read model and freshness

Every product, attention item, agent, build, and source error carries a `SourceRef`:

- `source` and `source_id` identify authority and record;
- `observed_at` records when the source was read;
- `freshness` is exactly `live`, `cached`, `stale`, or `unavailable`;
- `href` may point to an existing Hermes detail surface.

The dashboard plugin caches a snapshot for five seconds and labels worker heartbeats stale after fifteen seconds. If refresh fails after a successful read, cached records are retained and marked `stale`, with an explicit refresh error. If one adapter fails, `assemble_snapshot()` keeps contributions from other adapters and adds an `unavailable` source error. A first-build failure returns a safe empty snapshot with a critical error instead of an exception page.

Attention order is deterministic: severity first, then source identity. Build verdicts begin with recorded Agent Factory evidence (`PASS`, `FAIL`, `SKIPPED`, or `UNKNOWN`), but the Control Centre fails closed to `FAIL` when required manifest integrity cannot be verified.

## Public API

All routes inherit dashboard authentication and emit `private, no-store`, `nosniff`, no-referrer, and restrictive CSP headers. The MVP declares GET routes only.

| Endpoint | Response |
|---|---|
| `GET /api/plugins/control-centre/snapshot` | Complete public snapshot |
| `GET /api/plugins/control-centre/attention` | Attention records plus timestamp and source errors |
| `GET /api/plugins/control-centre/products` | Products plus timestamp and source errors |
| `GET /api/plugins/control-centre/agents` | Agents plus timestamp and source errors |
| `GET /api/plugins/control-centre/builds` | Build evidence plus timestamp and source errors |
| `GET /api/plugins/control-centre/health` | `ok` or `degraded` and per-source freshness |

The public projection removes repository filesystem paths, product knowledge roots, and agent workspace paths. It never exposes environment values, credentials, session messages, cron prompts/output, Kanban comments, or raw Agent Factory files.

## UI behavior

The plugin ships source under `dashboard/src/` and pre-built IIFE/CSS assets under `dashboard/dist/`. The default Overview is attention-first and includes builds, agents, products, source errors, and system links. Dedicated inspectors expose explicit allowlisted fields. Missing values render as `Not configured`; production environments are visually distinct. Product, agent, severity, and freshness filters persist while navigating between sections.

All navigation targets are relative dashboard links. The Control Centre has no mutation controls. Kanban, Chat, Sessions, Skills, Cron, Logs, System, and Plugins continue to own their existing actions.

## Deployment

Local use is the normal dashboard flow:

```bash
hermes dashboard
```

A named-profile dashboard remains a machine-level management surface unless `--isolated` is used. The API resolves `$HERMES_HOME` through `get_hermes_home()` and therefore respects profile isolation.

For remote browser or Desktop access, run the dashboard on a reachable host with the dashboard's authenticated remote-access configuration. A non-loopback dashboard must fail closed without an auth provider. Desktop can connect to that remote backend, but this MVP does not add a native Desktop Control Centre page; native promotion is a later milestone.

## Recovery

| Symptom | Recovery |
|---|---|
| Registry missing | Create `$HERMES_HOME/control-centre/products.yaml`; the empty state remains usable |
| Registry invalid | Fix the cited row/YAML; valid sibling products continue to render |
| Source unavailable | Follow the per-source recovery text and use the linked canonical page |
| Worker stale | Inspect the Kanban task/run and heartbeat; the Control Centre does not reclaim it |
| Auth expired | Sign in to the dashboard again and retry; the backend data is unchanged |
| Dashboard unavailable | Restart `hermes dashboard`; Kanban, profiles, sessions, cron, and factory evidence remain authoritative and unchanged |
| Cached refresh failure | Repair the source and refresh after the five-second cache window; stale data stays visibly marked |

## MVP non-goals

- No task/profile/build/deployment/approval mutation.
- No merge, push, deploy, rollback, or watchdog action.
- No GitHub PR/check ingestion.
- No Helm or business-data authority.
- No vault, memory, or skill content browsing or writes.
- No native Desktop page.
- No telemetry or second persistence layer.

Future milestones may add a read-only GitHub cockpit, vault/skill/memory browsing, typed approval policy, watchdog drills, and selected native Desktop views. Each requires a separate approval and must continue to preserve the source-of-truth boundary.
