# Hermes Agent Factory — Phase 7 MVP

Status: implemented (MVP scope below)
Package: `agent_factory/` (library) + `hermes_cli/agent_factory_cmd.py` (CLI edge command)
CLI surface: `hermes agent-factory validate|render|test|report|deploy`

## What this is

A CLI-only pipeline for turning a declarative `agent.yaml` spec into a
staged, tested, reviewed release, and — only under a strict, currently
unmet set of preconditions — a brand-new, factory-managed **test** Hermes
profile. It is explicitly **not** a core model tool: nothing in
`agent_factory/` is registered in `tools/registry.py`, so no agent can
call any part of this pipeline on itself or on another profile.

## States: specification → staged-release → deployment

These are three separate, non-overlapping artifacts. Nothing about the
canonical spec's *content* is inferred from what happens later.

1. **Specification** — a single `agent.yaml` file, the canonical
   intended-policy document. Validated by `agent_factory.schema`
   (`SPEC_API_VERSION = "agent-factory/v1"`). Declares:
   - `metadata.name` / `metadata.version` / `metadata.description`
   - `tools.allow` — a list of tool or toolset names (default-deny; see below)
   - `skills.required` — a list of local skill names (no wildcards)
   - `department` — optional, inert (see
     [`agent-factory-department-future.md`](agent-factory-department-future.md))

   Rejected outright: any top-level `deploy`, `approval`, `self_config`, or
   `cross_profile` field, any `tools.deny` key (the policy is default-deny,
   there is nothing to deny-list), and any wildcard (`*`/`all`) in
   `tools.allow` or `skills.required`.

2. **Staged release** — a directory produced by `agent_factory.staging.stage_release()`:
   - `agent.yaml` — a **byte-identical copy** of the reviewed spec source.
     It never gains derived or operational facts.
   - `rendered-config.json` — the *derived* artifact: the resolved
     default-deny effective tool list and required skill names. Clearly
     separate from the canonical file on purpose.
   - `skills/<name>/` — one directory per required skill, copied
     individually (see "Skill resolution" below).
   - `manifest.json` — every file's SHA256 plus one `combined_sha256`,
     built deterministically (sorted paths, no timestamps hashed).
   - `release-state.json` — written by the orchestrator; the first of five
     operational files, none of which ever touch `agent.yaml`.

3. **Deployment** — a *new* profile directory
   (`~/.hermes/profiles/aftest-<name>/`), created only by
   `agent_factory.deploy.deploy_release()` under the guardrails below.
   Deployment never edits an existing profile, so there is no deployment
   rollback path to build — refusal always leaves the filesystem exactly
   as it was.

## Default-deny effective tools (`agent_factory.effective_tools`)

`compute_effective_tools(spec.tools_allow)` resolves each declared entry
against the *live* `tools.registry` / `toolsets.TOOLSETS` — individual
tool names and toolset names both work, toolsets expand recursively. Two
toolsets are **always** forbidden, regardless of what a spec declares,
because they carry cron-scheduling or shared cross-profile kanban-board
authority: `cronjob`, `kanban` (`FORBIDDEN_TOOLSETS`). Anything not
resolved from `tools.allow` is denied — there is no separate deny-list to
maintain.

## Local skill resolution (`agent_factory.skills_resolve`)

Required skills are resolved **only** from the repo-local `skills/`
(bundled) and `optional-skills/` (optional) catalogs — never from any live
profile's `skills/` directory, and never over the network. Each resolved
skill directory is copied individually via `shutil.copytree` — attaching
`["alpha-skill"]` never pulls in sibling skills that happen to live in the
same catalog root ("no broad inheritance").

## Test layers (`agent_factory.tests_layer{1,2,3,4}`)

Evidence is `PASS` / `FAIL` / `SKIPPED` / `UNKNOWN`
(`agent_factory.state.Verdict`). A **mandatory** layer at `SKIPPED` or
`UNKNOWN` blocks deployment exactly like `FAIL` — see
`state.build_test_report`'s `deploy_allowed` computation, which requires
all four of `layer1_static` / `layer2_config` / `layer3_prompt` /
`layer4_kanban` to be present *and* `PASS`.

- **Layer 1 — static** (`tests_layer1.run_layer1_static`): pure schema
  validation of the spec dict. No filesystem, no registry, no network.
- **Layer 2 — rendered config + actual tool-schema exposure**
  (`tests_layer2.run_layer2_config`): re-derives the effective tool set
  from the *live* registry (`tools.registry.registry.get_definitions`),
  not from a config value we merely trust. Catches drift (a renamed/removed
  tool silently shrinking what the agent actually gets) and tampering (a
  hand-edited `rendered-config.json` claiming a forbidden toolset's tool —
  re-checked against live `toolsets.resolve_toolset`, not the artifact's
  own claim).
- **Layer 3 — limited, isolated prompt test runner**
  (`tests_layer3.run_prompt_scenario` / `run_layer3_prompt_tests`).
  **Scope, deliberately bounded: no real model is called, and no real tool
  handler is executed** — that would require the full workspace/session
  runtime, well outside this package's boundary, and would make the tests
  flaky. A `PromptScenario` scripts the tool calls a hypothetical
  completion *attempted*; the runner checks each against the effective
  policy gate (the same boundary Layer 2 already proved is real) and
  hashes any declared protected-state paths before/after. **The verdict is
  derived only from gate decisions and hash equality — it never inspects
  `scripted_response`.** `test_refusal_wording_alone_cannot_rescue_a_missed_block`
  (`tests/agent_factory/test_layer3.py`) is the regression test locking
  this in: a textbook-sounding refusal string cannot flip a `FAIL` to
  `PASS`. This does **not** claim to test model behavior — only that the
  tool-exposure boundary a real model would be constrained by is enforced
  as declared.
- **Layer 4 — Kanban review / approval / deployment-control**
  (`tests_layer4.py`): creates a kanban `review_required` task
  (`create_review_task`) whose body embeds the release's
  `manifest_combined_sha256` ("hash binding" — approve-then-swap is
  detected: `verify_hash_binding` fails if the release was re-staged after
  approval). The decision is always re-derived live from the task's kanban
  **event log** (`read_live_review_decision`), never trusted from a
  separately-stored field. `evaluate_deployment_gate` additionally calls a
  `ProvenanceVerifier` (below) and only passes when decision == `"approve"`
  **and** the hash still matches **and** provenance is trusted.

## Approval provenance (`agent_factory.provenance`)

"No manual `approval.json` or identity string is trusted." A
`ProvenanceVerifier` is the only thing that can turn a kanban decision into
deploy-eligible trust. The shipped `DefaultFailClosedVerifier` **always**
returns `trusted=False` — there is no real identity provenance source
(SSO, hardware key, signed attestation) wired in yet, so it fails closed
for every release, unconditionally, regardless of `claimed_identity`. Tests
inject an alternate stub verifier to exercise the guarded success path;
the CLI never offers a way to do so (see below).

## Operational files (`agent_factory.state`)

Five files live alongside a staged release, and `agent.yaml` is never one
of them:

| File | Written by | Content |
|---|---|---|
| `release-state.json` | `orchestrator.render` | release id, spec name/version, manifest hash, status |
| `test-report.json` | `orchestrator.run_tests` | four `LayerEvidence` blocks + `overall_verdict` + `deploy_allowed` |
| `review-packet.json` | `orchestrator.build_report` | spec + manifest + effective-tools + skills + test-report summary + version comparison, for a human reviewer |
| `approval.json` | not directly written by the orchestrator; `agent_factory.state.ApprovalRecord` exists for callers that want an audit record of a gate evaluation | decision, reviewer, provenance result — always re-derived, never the source of truth at deploy time |
| `deployment-record.json` | `orchestrator.deploy` | outcome (`deployed`/`refused`), target profile, failure reason, verified identity — written on **every** attempt, success or refusal |

## Guarded deploy (`agent_factory.deploy`)

`deploy_release()` fails closed unless **all** of the following hold, checked
in order, each producing a distinct refusal reason:

1. the target profile name starts with `TEST_PROFILE_PREFIX = "aftest-"`
   (this is the "one NEW factory-managed TEST profile only" guardrail);
2. that profile does not already exist (**collision refusal** — an
   existing profile, factory-managed or not, is never touched, so there is
   no rollback path to implement);
3. the release's `test-report.json` says `deploy_allowed`;
4. a kanban review task id was supplied, and
   `tests_layer4.evaluate_deployment_gate` passes (live "approve" +
   matching hash binding + trusted provenance).

Profile contents are always assembled in a `tempfile.mkdtemp()` directory
first; only after every precondition above holds does
`shutil.move()` commit it into place. **Any** failure — a precondition, or
an I/O error partway through building the temp directory — removes the
temp directory and leaves nothing behind; `deployment-record.json` records
what happened either way.

## CLI (`hermes_cli/agent_factory_cmd.py`)

`hermes agent-factory validate|render|test|report|deploy` — a thin
argparse layer over `agent_factory.orchestrator`, which is the one place
that writes the operational files described above. Two properties worth
being explicit about:

- **Generation/render/test/report/Kanban completion never deploys.**
  Each subcommand calls exactly one orchestrator function; nothing chains
  into `deploy_release` implicitly.
- **`hermes agent-factory deploy` always uses `DefaultFailClosedVerifier`.**
  There is no CLI flag to inject a different verifier — that capability
  only exists in the Python API (`agent_factory.deploy.deploy_release(...,
  verifier=...)`), used by tests to exercise the guarded success path.
  Until a real provenance integration is built and *deliberately* wired
  into the CLI, `hermes agent-factory deploy` cannot succeed no matter what
  a kanban task says or what `approval.json` contains
  (`tests/hermes_cli/test_agent_factory_cli.py::test_deploy_always_refuses_via_cli_even_with_kanban_approval`).

## What this MVP deliberately does not do

- No semantic judging of model output (Layer 3 never inspects response text for verdict purposes).
- No real model or real tool-handler execution in Layer 3.
- No remote skill fetching, no MCP wiring, no cron toolset, no broad ("all"/"*") toolsets or bundled-skill wildcards.
- No rollback logic (deploy only ever creates a brand-new profile; it never modifies one).
- No runtime enforcement claims beyond what Layer 2 actually re-checks against the live tool registry.
- No department.yaml runtime — see [`agent-factory-department-future.md`](agent-factory-department-future.md).
