# Decision Log

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index. This is the amendment mechanism referenced in [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md) §7.*

Architectural Decision Records (ADRs), newest-relevant-context first within each entry. Each record: **Status**, **Context**, **Decision**, **Consequences**.

---

## ADR-0001 — Async-first architecture, no thread bridging

**Status:** Accepted (carried from legacy prototype's failure)
**Context:** the legacy WatchFlow prototype bridged a blocking `watchdog` observer into asyncio via threads, leading to race conditions, inconsistent shutdown behavior, and blocking SQLite calls on the event loop.
**Decision:** the engine is async top to bottom; no dependency without an async surface is used on a hot path without an explicit, documented, isolated adapter boundary.
**Consequences:** every core dependency choice (`watchfiles`, `aiosqlite`, `httpx`) was selected specifically for its async-native surface, narrowing the field of otherwise-popular libraries.

---

## ADR-0002 — Strict subprocess execution, no `shell=True`

**Status:** Accepted
**Context:** shell-glue tools and the legacy prototype both allowed shell string interpolation, which becomes a code-execution vector the moment any part of the invoked command derives from external input (a filename, a webhook payload, an agent's tool-call argument).
**Decision:** `asyncio.create_subprocess_exec` only, `argv` as `list[str]`, enforced by a custom CI-blocking lint rule.
**Consequences:** slightly more verbose config for multi-command shell pipelines (must be expressed as DAG steps instead of `&&`-joined strings) in exchange for eliminating an entire vulnerability class outright.

---

## ADR-0003 — Broaden scope from filesystem-only reactive engine to cross-platform event-driven workflow orchestration

**Status:** Accepted — **2026-07-07**
**Context:** the original WatchFlow spec scoped the engine to filesystem events only. In practice, every downstream use case that mattered — CI/CD, DevOps automation, and now AI agents calling into local execution via MCP — needed the *same* pipeline (match → schedule → execute → record → observe) applied to event sources that aren't filesystem writes: cron ticks, webhooks, queue messages, git pushes, and MCP tool calls.
**Decision:** generalize the engine's core abstraction from "file event" to "event," with the filesystem watcher demoted to one Source Adapter among several. The public positioning becomes: *a cross-platform, event-driven workflow orchestration engine for local development, CI/CD, DevOps automation, and MCP-powered AI systems.*
**Consequences:** this forced two terminology renames (ADR-0004), a new core module (the MCP Gateway, ADR-0005), and a restructuring of the documentation from one monolithic spec into the twelve focused documents indexed in [`WATCHFLOW.md`](./WATCHFLOW.md). No change to the async-first, safe-execution, or event-driven tenets — those generalize unchanged across every event source.

---

## ADR-0004 — Rename `Intent` → `Trigger`, `Pipeline` → `Workflow`

**Status:** Accepted — **2026-07-07** (companion to ADR-0003)
**Context:** "Intent" and "Pipeline" were meaningful names for a file-change-detection engine but don't read naturally once the engine matches cron ticks, webhooks, and MCP tool calls against declared rules, or when its own tagline is "workflow orchestration engine."
**Decision:** rename the pattern-matching rule from `Intent` to `Trigger`, and the executable unit from `Pipeline` to `Workflow` (a linear pipeline becomes a single-branch Workflow, not a separate concept).
**Consequences:** every document, config key, and CLI surface produced after this decision uses the new terminology consistently; there is no dual-naming transition period in the documentation, though the shipped `v0.x` implementation will need a one-time config-schema migration note when it reaches that point in the roadmap.

---

## ADR-0005 — MCP as a core module, not a plugin

**Status:** Accepted — **2026-07-07**
**Context:** Article V of the constitution says the core stays minimal and domain-specific integrations are plugins. MCP could have been built as `watchflow-mcp`, an official plugin, consistent with how Slack or GitHub integrations are handled.
**Decision:** MCP is core (the `MCPGateway`, in both server and client modes) rather than a plugin, because it is not a domain-specific *integration* the way Slack or PagerDuty are — it is a *calling convention*, on par with the CLI or the embeddable `Engine` API, for a caller class (AI agents) the constitution explicitly names as a first-class audience (Article VII).
**Consequences:** the core engine now has a direct dependency on the MCP Python SDK; this is judged acceptable because the SDK's surface (schema validation, tool/resource definitions) is a stable, standards-governed contract, not a vendor-specific one. Individual *bundles* of curated tools for specific agent frameworks remain plugins (`watchflow-mcp-agents`), preserving the minimal-core principle at the right boundary.

---

## ADR-0006 — Source adapter tiering: core vs. plugin

**Status:** Accepted
**Context:** every additional bundled Source Adapter is either an unconditional core dependency or a trust/security decision (opening a network listener, connecting to a broker) that shouldn't be silently on-by-default.
**Decision:** Filesystem, Cron, Manual, and MCP-trigger adapters are core-bundled because none require an external service or introduce a new network trust boundary. Webhook, Queue, Git, and CI-provider adapters are official first-party plugins, requiring an explicit extra (`[webhooks]`, `[queue]`, etc.) or capability grant to activate.
**Consequences:** a fresh `pip install watchflow` opens no network listener and trusts no external broker by default — the safest possible out-of-the-box posture, at the cost of one extra install step for DevOps/CI use cases that need those sources.

---

## ADR-0007 — `aiosqlite` as the embedded EventStore backend

**Status:** Accepted
**Context:** candidates considered: embedded Postgres, a pure key-value embedded store, and `aiosqlite`.
**Decision:** `aiosqlite`, WAL mode, batched writes — chosen for zero external service dependency (Article VI, local-first) while still supporting the ad-hoc `query_event_history` access pattern the CLI, TUI, and MCP server surface all need.
**Consequences:** at very high sustained event volumes, WAL-mode SQLite is not a substitute for a dedicated time-series store; teams with that need are expected to use the (opt-in) OpenTelemetry/Prometheus exporters and their own backend rather than querying the EventStore directly at scale.

---

## ADR-0008 — TUI remains a separate, optional consumer

**Status:** Accepted (carried from legacy prototype's failure)
**Context:** the legacy prototype's TUI rendering was coupled directly to the reactive loop, making the engine untestable headless and the TUI untestable without a live engine.
**Decision:** the TUI subscribes to the same observability bus as every other exporter (Prometheus, OpenTelemetry, structlog) and has no privileged access to internal engine state.
**Consequences:** the engine runs identically on a headless server with no terminal attached; the TUI can be developed and tested against a fake observability feed independent of the engine's own release cadence.

---

## ADR-0009 — Provenance tagging distinguishes AI/MCP-initiated Runs from human/cron-initiated ones

**Status:** Accepted — **2026-07-07**
**Context:** once an AI agent is a first-class caller (ADR-0005), an audit trail that can't distinguish "a person ran this" from "an agent's tool call ran this" undermines the trust the constitution promises in Article VII.
**Decision:** every `Run` carries `mcp_origin` metadata when applicable, recorded unconditionally in the EventStore (see [`SECURITY_MODEL.md`](./SECURITY_MODEL.md) §5, [`MCP_INTEGRATION.md`](./MCP_INTEGRATION.md) §3).
**Consequences:** slightly larger EventStore records; in exchange, "which of today's Runs were AI-initiated" is always a queryable fact, not a forensic reconstruction.

---

## Governance timeline

**Phase 1 — BDFL (`v0.1.0` → `v2.3.0`).** San Shibu (`ScorpioCodeX`) is sole maintainer; all merges, all decisions. RFCs are welcome but not procedurally required. Constitutional amendments in this phase still require a recorded ADR with explicit rationale (see [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md) §7) — the bar stays high even without a Core Team to enforce it.

**Phase 2 — RFC + Core Team (`v3.0.0` onward).**
- **Core Team:** 3–5 active maintainers with merge rights.
- **RFC process:** substantial changes require an RFC in `rfcs/`, accepted by 2/3 of the Core Team.
- **BDFL backstop:** retains tiebreaker for 12 months post-transition, then full Core Team consensus.

| Decision type | Process |
|---|---|
| Bugfix | Single Core Team approval |
| Minor feature | Single Core Team approval |
| API change | RFC + 2/3 Core Team |
| Major version theme | RFC + supermajority + 2-week public comment |
| Governance change | RFC + supermajority + 4-week public comment |
| Constitutional amendment | RFC + supermajority + 4-week public comment + recorded ADR here |
