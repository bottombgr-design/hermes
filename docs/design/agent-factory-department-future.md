# Future Agent Factory Department Schema

Status: documentation only; not implemented in Phase 7.

Phase 7 treats an agent specification's optional `department` value as inert
catalog metadata. It is retained only in canonical `agent.yaml`; it is not
rendered into `SOUL.md`, `config.yaml`, or `rendered-config.json`, and it has no
runtime, permission, skill, deployment, or coordination effect.

## Possible future catalog layout

```text
departments/
  <department-id>/
    department.yaml
    agents/
      <agent-id>/
        agent.yaml
```

This layout would organize specifications only. Creating a department would
not create or deploy agents automatically.

## Proposed `department.yaml`

```yaml
apiVersion: agent-factory/v1
kind: DepartmentSpec
metadata:
  id: engineering
  display_name: Engineering
specification:
  mission: "Department mission"
  shared_boundaries:
    - "Boundary applied to department specifications"
  shared_skills:
    required: []
    optional: []
  allowed_agent_types:
    - reviewer
    - builder
  escalation_map:
    security: sentinel
    code: tobias
```

## Future validation rules

- `metadata.id` would be a lowercase slug.
- Department membership would remain explicit in each canonical `agent.yaml`.
- Department defaults could restrict an agent but could never silently expand
  its tool, skill, memory, network, MCP, deployment, or approval authority.
- Every agent would still declare and pass validation for its final effective
  permission allowlist.
- Shared skills would still resolve into explicit per-agent attachments; no
  broad inheritance would occur.
- Cross-department handoffs would use Kanban contracts.
- Department coordinators could not approve their own deployment or the
  deployment of an agent they generated without an independently verified
  human approval path.
- Final authority would remain with a human operator.

## Explicitly not implemented in Phase 7

- Department schema parsing or validation.
- Department runtime behavior or isolation.
- Permission or skill inheritance from departments.
- Department coordinators or autonomous governance.
- Automatic agent creation, deployment, routing, or gateway configuration.
- Marketing, Finance, Operations, Customer Service, Engineering, or other
  department instances.

Any future implementation requires a separate proposal, tests, review, and
human approval.