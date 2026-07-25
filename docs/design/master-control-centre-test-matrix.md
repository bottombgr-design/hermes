# Master Control Centre — Phase 8 verification matrix

Status: F1/F2 blocking findings remediated; final independent operator-lane re-review not run
Branch: `phase8-master-control-centre`
HEAD: `072af9b70cf198587b90d6e8d3c671d5c7a8e7d3`

This matrix separates automated, fixture, real-browser, and Desktop evidence. A state is not browser-proven merely because its JSON or bundle test passes. All paths below are disposable Phase 8 evidence; no live/operator business data was used.

## Required behavior states

| State | Automated evidence | Browser/Desktop evidence | Gate |
|---|---|---|---|
| Normal overview with multiple products | Focused Phase 8 suite: 63/63 PASS | Chromium 149 rendered Atlas/Helm overview, Agents, and Products; `/tmp/phase8-control-centre-evidence/browser-report.json` | PASS — browser-proven fixture |
| Empty registry | UI harness empty-state contract PASS | Chromium rendered `No products configured`; `/tmp/phase8-control-centre-evidence/empty-registry-browser-report.json` and `browser-empty-registry-1440x1000.png` | PASS — browser-proven fixture |
| Valid product plus invalid/incomplete product | Registry tests preserve valid siblings and recovery errors | Chromium rendered Atlas configured, Helm incomplete, and invalid-registry recovery; `browser-report.json` | PASS — browser-proven fixture |
| Running worker with fresh heartbeat | Kanban/profile adapter tests PASS | Chromium rendered the fresh worker fixture; `browser-agents-1440x1000.png` | PASS — browser-proven fixture |
| Stale worker | Adapter freshness and attention tests PASS | Chromium rendered `stale-worker`; `browser-agents-1440x1000.png` | PASS — browser-proven fixture |
| Blocked/review-required task | Attention ordering and UI harness tests PASS | Chromium rendered blocked Atlas and review-required Helm tasks; `browser-report.json` | PASS — browser-proven fixture |
| Remediated partial Kanban failure | Profile-source regression proves roster survives failed activity join | Chromium rendered four profiles beside `Agent activity unavailable: OperationalError`, Kanban source warning, and both recovery messages; `/tmp/phase8-control-centre-evidence/remediated-partial-kanban-failure-snapshot.json`, `remediated-partial-kanban-failure-browser-report.json`, and `browser-remediated-partial-kanban-failure-1440x1000.png` | PASS — browser-proven fixture; supersedes the pre-remediation FAIL artifact without reclassifying it |
| Product filtering | UI harness and Chromium prove selected-product exclusion for attention records; the browser retained unassociated agents, diagnostics, and the unassigned build | Real Chromium selected Atlas and hid the Helm-associated `Review required` attention item. Agent-to-product and build-to-product association are not implemented, so their retained visibility does not prove field-level exclusion semantics; that association work is deliberately deferred. Evidence: `/tmp/phase8-control-centre-evidence/product-filter-fixture-snapshot.json`, `product-filter-browser-report.json`, and `browser-product-filter-atlas-1440x1000.png` | PARTIAL — browser-proven for attention exclusion only |
| Factory PASS/FAIL/SKIPPED/UNKNOWN | Adapter regression slice and four-verdict UI harness PASS | `/tmp/phase8-control-centre-evidence/agent-factory-four-verdict-ui-report.json`; not separately pixel-captured in Chromium | PASS — fixture/UI-harness proof only |
| Narrow viewport | Responsive CSS contract PASS | Chromium 390×844: inner/document width 390 px, no horizontal overflow, one-column grid; `browser-report.json` and `browser-overview-390x844.png` | PASS — browser-proven fixture |
| Desktop remote-backend connection | Existing Desktop/dashboard transport gates were previously green | Prior built Electron preload/IPC proof: `/tmp/phase8-control-centre-evidence/desktop-report.json` and `desktop-remote-connected.png`; not rerun during remediation closure | PASS — prior transport evidence, not a Desktop-native Control Centre page |
| Auth rejection and recovery | Dashboard auth regression coverage | Real Chromium invalid-token request returned 401, then valid session token returned 200; `browser-report.json` | PASS — deliberate invalid-token simulation, not wall-clock expiry |
| Dashboard restart preserves Kanban | Read-only route and snapshot tests PASS | Prior restart evidence preserved semantic counts and Kanban SHA-256 `d1f20e839fb75124c829de99982d0012482dacad60e7f5c1e4fdc442fc8a8eca`; `restart-before-snapshot.json`, `restart-after-snapshot.json`, `restart-before-kanban.sha256`, `restart-after-kanban.sha256` | PASS — fixture proof |

## Final automated commands

| Command | Result | Evidence |
|---|---|---|
| `scripts/run_tests.sh tests/control_centre tests/plugins/test_control_centre_plugin.py tests/plugins/test_control_centre_ui.py tests/test_phase8_protected_profile_gate.py -q` | PASS — 72 passed, 0 failed, 8 files | `/tmp/phase8-control-centre-evidence/closure-phase8-tests-f2.log` |
| `scripts/run_tests.sh tests/agent_factory tests/plugins/test_kanban_dashboard_plugin.py tests/hermes_cli/test_agent_factory_cli.py -q` | PASS — 306 passed, 0 failed, 16 files | `/tmp/phase8-control-centre-evidence/closure-phase7-regressions.log` |
| `scripts/run_tests.sh tests/test_packaging_build_guard.py -q` | PASS — 5 passed, 0 failed; wheel imported outside checkout | `/tmp/phase8-control-centre-evidence/closure-packaging-import.log` |
| `npm --workspace web run check` | PASS — typecheck, 97 Vitest tests, ESLint 0 errors/24 pre-existing warnings | `/tmp/phase8-control-centre-evidence/closure-web-check.log` |
| `npm --workspace web run build` | PASS — TypeScript and Vite production build | `/tmp/phase8-control-centre-evidence/closure-web-build.log` |
| Dashboard JavaScript syntax, source/dist parity, manifest parse, Python compile, whitespace | PASS | `/tmp/phase8-control-centre-evidence/closure-integrity-gates.txt` |

The repository-wide suite was not rerun during this closure. Earlier evidence recorded unrelated baseline/environment failures outside the Phase 8 delta; no unrelated code was changed to conceal them.

## Protected-profile evidence

| Evidence | Classification |
|---|---|
| Historical raw byte gate | **FAIL preserved** — Tobias `skills/.usage.json` changed. Original artifacts remain `/tmp/phase8-protected-profile-gate.txt` and `/tmp/phase8-protected-profiles-after.json`. This result is not reclassified. |
| Normalization implementation | `scripts/phase8_protected_profile_gate.py`; retains `state`, `pinned`, `created_by`, `agent_created`, `archived_at`, and `created_at`; ignores only documented volatile counters/timestamps for gating; records raw and normalized hashes; fails closed on missing, malformed, non-object, or undocumented fields. |
| Normalization and compare tests | PASS — 22 tests, included in the 72-test Phase 8 gate; covers every retained field, all documented volatile fields, skill-key changes, missing data, malformed data, unknown fields, empty expected sets, foreign schemas, profile addition/removal, policy drift, and volatile-only drift. Malformed/foreign compare inputs return controlled FAIL output and exit 1 without a traceback. |
| Fresh normalized before/after run | PASS — all seven protected profiles; Tobias raw aggregate changed after a deliberate mandatory skill load while its normalized policy aggregate remained unchanged. Evidence: `/tmp/phase8-protected-normalized-before.json`, `phase8-protected-normalized-before.log`, `phase8-protected-normalized-after.json`, `phase8-protected-normalized-after.log`, `phase8-protected-normalized-comparison.json`, and `phase8-protected-normalized-gate.txt`. |
| Historical normalized comparison | NOT RUN — the legacy before-write artifact stored only raw hashes, not the prior `.usage.json` policy projection, so a historical normalized hash cannot be reconstructed honestly. The new PASS proves field-level semantics and counters-only drift across the fresh controlled interval; it does not rewrite the historical raw FAIL. |

## Integrity and scope gates

| Gate | Result |
|---|---|
| Wheel includes the Python `control_centre` package and isolated import works | PASS — this does not prove packaging of `plugins/control-centre/dashboard/**`; bundled hyphenated plugin assets are outside the wheel assertion and remain deliberately deferred with the existing plugin-packaging pattern |
| Control Centre plugin API GET-only and no-store | PASS — runtime route tests |
| Kanban opened with SQLite `mode=ro`, `uri=True`, and `PRAGMA query_only=ON` | PASS — missing DB not created; writes rejected; bytes unchanged |
| JavaScript and CSS source/distribution byte parity | PASS |
| Disposable fixture PID/address verified before shutdown/restart | PASS — process cwd, command, `HOME`, `HERMES_HOME`, `HERMES_KANBAN_DB`, listener PID, and health endpoint matched the Phase 8 fixture |
| Desktop-generated Git drift | PASS — no changed paths under `apps/desktop`; final status packet is authoritative |
| Commit / merge / push / deploy / dependency install / live-data mutation | PASS — none performed |

## Review status

The follow-up independent Claude Code Layer-4 operator review is **NOT RUN**. Marc must launch it separately. The complete packet is `/tmp/phase8-independent-review-packet-remediated`; Tobias self-review, prior review, browser tooling, and automated tests do not satisfy that gate.

## Residual proof limits

- Agent Factory four-verdict rendering is UI-harness proven, not four-card Chromium pixel proof.
- Desktop evidence proves remote transport/API access, not a Desktop-native Control Centre page, and was not rerun in this closure.
- Authentication evidence is rejection plus recovery using a deliberately invalid token, not natural token expiry.
- All browser evidence uses disposable fixture data, not live/operator data or production-scale Kanban.
- The historical raw protected-profile FAIL remains unresolved as raw bytes; only the fresh field-normalized interval is green.
- Product-filter browser evidence proves exclusion for attention only. Agent-to-product and build-to-product association are not implemented and are deliberately deferred.
- Packaging/import proof covers the Python `control_centre` package, not the hyphenated dashboard plugin assets. Source/dist parity proves copy consistency, not a generated build output.