# MCP Integration

*Part of the ScorpioWatch documentation set — see [`SCORPIOWATCH.md`](./SCORPIOWATCH.md) for the full index. Security invariants referenced here are defined in [`SECURITY_MODEL.md`](./SECURITY_MODEL.md); module interfaces in [`MODULE_SPECIFICATIONS.md`](./MODULE_SPECIFICATIONS.md) §7.*

---

## 1. Why this document exists

Article VII of [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md) states that MCP is a first-class citizen of ScorpioWatch, not a plugin bought later. This document is where that commitment becomes concrete: what ScorpioWatch exposes to AI agents, what it consumes from them, and the safety controls that keep an AI agent's tool call held to the same standard as a human running a command by hand.

**A brief primer, for readers unfamiliar with MCP:** the Model Context Protocol is an open standard (originally released by Anthropic, now governed under the Agentic AI Foundation at the Linux Foundation) for connecting AI models and agents to external tools and data sources, using **servers** (which expose *tools* an agent can call and *resources* an agent can read) and **clients** (the agent-side connector that calls into servers), speaking JSON-RPC 2.0 over one of two transports: **stdio** (a local subprocess — the natural fit for an agent embedded in an IDE or a CLI coding assistant on the same machine) or **Streamable HTTP** (a remote, network-reachable server — the fit for a CI-triggered or hosted agent). ScorpioWatch's `MCPGateway` supports both, and implementation should always verify behavior against the current official MCP specification, since the protocol continues to evolve — see §7 for a note on the transition underway as of this writing.

---

## 2. Two integration modes

```
                     ┌──────────────────────────────┐
   AI agent /   ───▶ │   MCP Gateway — SERVER mode  │ ───▶  Scheduler → DAGExecutor
   MCP client        │  exposes ScorpioWatch actions│       (a Run, provenance-tagged)
                     └──────────────────────────────┘

                     ┌──────────────────────────────┐
   Workflow Step ───▶ │  MCP Gateway — CLIENT mode   │ ───▶  external MCP tool/server
   (kind=mcp_tool)    │ calls out on ScorpioWatch's  │       (result validated, then
                     │  behalf                      │        fed to downstream Steps)
                     └──────────────────────────────┘
```

### A. ScorpioWatch as MCP server

ScorpioWatch exposes a curated, permissioned subset of its own capabilities as MCP tools and resources:

**Tools:**
- `trigger_workflow(name, params)` — start a Run of a named Workflow.
- `get_run_status(run_id)` — poll a Run's current state.
- `list_triggers()` — enumerate registered Triggers and their target Workflows.
- `query_event_history(filters)` — read from the EventStore.
- `cancel_run(run_id)` — cancel an in-flight Run.

**Resources:**
- A live subscription feed of Events and Run status changes.
- Run logs, scoped to the requesting caller's granted permissions.
- Workflow definitions (read-only).

**Use case:** an AI coding agent embedded in an IDE, instead of shelling out to run `pytest` directly, calls `trigger_workflow("run-tests")`. It gets ScorpioWatch's dedupe, cooldown, structured logging, and audit trail for free — and never needs raw shell access on the developer's machine.

### B. ScorpioWatch as MCP client

A Workflow `Step` of kind `mcp_tool` calls an external MCP server as part of a DAG, exactly like a `subprocess` step calls a binary:

```toml
[[trigger]]
name = "on-push-review"
source = "git"
match = { event = "push" }

  [[trigger.workflow.step]]
  kind = "mcp_tool"
  server = "code-review-agent"
  tool = "review_diff"
  args = { ref = "HEAD" }

  [[trigger.workflow.step]]
  kind = "plugin"
  plugin = "scorpiowatch-slack"
  depends_on = ["on-push-review.0"]
```

**Use case:** on a git push event, a Workflow step calls out to an external, MCP-exposed code-review tool, then a downstream step posts the results to Slack — all orchestrated with the same DAG semantics as a purely local Workflow.

---

## 3. Provenance and audit

Every `Run` that either originated from an MCP tool call, or that itself calls out to an external MCP tool, carries `mcp_origin` metadata into the `EventStore`:

```json
{
  "mcp_origin": {
    "direction": "inbound | outbound",
    "caller_identity": "agent-id-if-available",
    "tool_name": "trigger_workflow",
    "protocol_version": "…",
    "server": "code-review-agent"
  }
}
```

This is what lets `swatch history --mcp-only` (or the TUI's Observe view) answer "which of today's Runs were AI-initiated" without guesswork.

---

## 4. Trust and safety for AI-initiated Runs

Restating [`SECURITY_MODEL.md`](./SECURITY_MODEL.md) §5 in workflow-authoring terms:

- A destructive `Step` (deletes, force-pushes, production deploys) is unreachable via an MCP tool call unless its Trigger explicitly sets `allow_mcp_trigger: true`.
- `requires_confirmation: true` blocks a Step's execution pending explicit human acknowledgment — surfaced in the TUI or via a configured notification exporter — even if the triggering agent is otherwise permitted.
- MCP-originated Runs are rate-limited on a counter separate from human/cron-originated Runs, so a looping or misbehaving agent cannot starve the Scheduler for everyone else.
- Arguments arriving via an MCP tool call are schema-validated with the same `pydantic` discipline as a webhook payload before they can influence a Step's `argv` — never string-interpolated.

---

## 5. Configuration surface

```toml
[mcp.server]
enabled = true
transport = "stdio"                # "stdio" (local agent, e.g. an IDE assistant) or
                                    # "streamable_http" (remote/CI-triggered agent)
expose = ["trigger_workflow", "get_run_status", "query_event_history"]
rate_limit_per_min = 30

[mcp.client]
enabled = true

  [[mcp.client.servers]]
  name = "code-review-agent"
  transport = "stdio"
  command = ["code-review-mcp-server"]
```

---

## 6. CLI surface additions

```
swatch mcp serve                start the MCP server gateway
swatch mcp tools list           show exposed tools and their permission requirements
swatch mcp client test SERVER   verify connectivity to a configured external MCP server
```

---

## 7. Compatibility and versioning

The MCP Gateway tracks the MCP protocol version it was built against and negotiates capabilities at connection time; an incompatible client or server fails the connection with an explicit version-mismatch error rather than attempting undefined behavior. Because the protocol is still evolving, the Gateway's exact tool/resource surface is expected to grow across releases — see [`ROADMAP.md`](./ROADMAP.md) for the version this first ships stable in, and [`DECISION_LOG.md`](./DECISION_LOG.md) ADR-0005 for why it's core rather than a plugin.

**A note on timing:** as of this writing, the MCP specification is mid-transition to a new revision (2026-07-28) that moves the protocol core to a stateless model — dropping the long-lived session in favor of a design that scales behind an ordinary load balancer, alongside a formal Extensions framework (long-running work, richer server-to-client interaction patterns) and tightened, OAuth/OIDC-aligned authorization for remote servers. None of this changes ScorpioWatch's own design: because a `Run`'s state already lives in the `EventStore` rather than in memory tied to a connection (§1 of [`EXECUTION_MODEL.md`](./EXECUTION_MODEL.md)), the `MCPGateway` was never depending on the MCP session as a place to keep state in the first place. The practical implication is mainly on the security side — remote (`streamable_http`) deployments should track the tightened authorization guidance as it stabilizes, which is a `SECURITY_MODEL.md` concern, not an `EXECUTION_MODEL.md` one.
