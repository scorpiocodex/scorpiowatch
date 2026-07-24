# Roadmap

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index.*

Five stable majors, same discipline as the original filesystem-only plan, now carrying the broader scope of [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md). LTS is **18 months** for v1–v4, **36 months** for v5. Each band's final minor overlaps with the next major's release-candidate cycle.

---

## Planning basis — capacity and how targets are expressed

WatchFlow is built by **one maintainer, part-time** (~10 focused hours per week) alongside a full-time job. The targets on this roadmap were previously absolute calendar months ("target: month 4"). Those were set before ADR-0003 roughly doubled v1.0.0's scope, were never revised, and — being bare calendar dates with no stated effort behind them — meant nothing without knowing the capacity assumed.

This roadmap therefore expresses every target as **cumulative effort-weeks from project start**, where **one effort-week = 40 hours of focused work** (one full-time-equivalent week). At the ~10 h/week capacity above, roughly one effort-week is completed per calendar month — but that ratio is deliberately kept *out* of the targets. An effort-week measures work delivered, not time elapsed, so a slow month or a burst of free time re-times the calendar without invalidating the plan.

Targets are estimates and are **revisable at any time; revising a target does not require an ADR** — only changing the capabilities in a band, or their sequencing across bands, does (see ADR-0011). LTS windows are the exception: they are commitments measured in calendar months from each release and are not effort-based.

---

## Pre-release band — `v0.1.0` → `v0.3.2`

> Bootstrap to release candidate. No public stability guarantees.

**v0.1.0 — Bootstrap (target: 4 effort-weeks)**
- Project scaffolding per [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md); MIT license, base CI
- `FilesystemAdapter` (Linux only), `ManualAdapter`
- `TriggerEngine` with glob patterns, no scoring yet
- `Executor` with single subprocess, `shell=False`, no timeout
- `watchflow run` and `watchflow init`

**v0.1.1** — bugfix: adapter not closing cleanly on SIGINT
**v0.1.2** — bugfix: subprocess zombies on cancellation

**v0.2.0 — EventBus + Storage skeleton (target: 7 effort-weeks)**
- Bounded `EventBus` with backpressure strategies
- `EventStore` skeleton (`aiosqlite`, no batching yet)
- macOS `FilesystemAdapter` support
- `CronAdapter` (core-bundled)
- Basic confidence scoring in `TriggerEngine`

**v0.2.1** — bugfix: queue overflow not raising `BackpressureError`
**v0.2.2** — bugfix: macOS `FSEvents` missing initial scan

**v0.3.0 — TriggerEngine + Scheduler (target: 10 effort-weeks)**
- `Scheduler` (rate limit, dedupe, cooldown)
- Windows `FilesystemAdapter` support (`ReadDirectoryChangesW`)
- `MCPTriggerAdapter` + minimal MCP server mode (`trigger_workflow` only)
- `watchflow check` and `watchflow doctor`

**v0.3.1** — bugfix: dedupe key collisions for similar paths
**v0.3.2** — release candidate for v1.0.0: doc freeze, API freeze, soak testing (target: 12 effort-weeks)

---

## Stable 1 band — `v1.0.0` → `v1.3.0`

> First stable major: core MVP for local development, then the DevOps unlock — daemon and operability, the plugin host carrying the webhook source adapter, metrics, and container packaging. Serves both co-equal primary audiences within the first stable band (ADR-0011). **LTS until 18 months post-release.**

**v1.0.0 — Core MVP stable (target: 15 effort-weeks)**
- Filesystem, cron, and manual adapters; linear (single-branch) Workflows; `TriggerEngine`; `Scheduler`; `Executor`; `EventStore`; CLI — fully usable for local development
- Public API freeze for `Engine`, `Workflow`, `Trigger`, `Step` (the embedded-library topology is usable from here)
- 80%+ test coverage; Linux/macOS/Windows wheels on PyPI
- Signed GitHub Release with SHA-256 checksums

**v1.0.1** — bugfix: race in `Scheduler.cooldown_check`
**v1.0.2** — bugfix: `EventStore` migration on first run

**v1.1.0 — Daemon + operability (target: 19 effort-weeks)**
- `watchflow daemon` (systemd/launchd-managed), Unix socket / named pipe IPC, crash-only recovery reconciled against the `EventStore`
- Per-component health endpoints (liveness/readiness), bind address configurable
- Graceful shutdown on `SIGTERM`/`SIGINT` with a bounded drain window (see [`EXECUTION_MODEL.md`](./EXECUTION_MODEL.md) §7)
- Process exit-code semantics for CI composition (see [`EXECUTION_MODEL.md`](./EXECUTION_MODEL.md) §7)
- Environment-variable configuration override (see [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md) §3)
- **This is the DevOps unlock.**

**v1.1.1** — bugfix: WAL checkpoint hang under sustained writes
**v1.1.2** — bugfix: daemon socket permissions on restart
**v1.1.3** — bugfix: health endpoint not respecting bind address

**v1.2.0 — Plugin host + webhook source adapter (target: 22 effort-weeks)**
- `PluginHost` with entry-point discovery and capability grants
- Plugin sandboxing (no `subprocess`/`network` access without explicit grant)
- `watchflow-webhook` ships as the first official source-adapter plugin — the plugin host is pulled forward to here so the webhook adapter arrives **as a plugin, never promoted into core** (ADR-0006, ADR-0011)

**v1.2.1** — bugfix: plugin load order non-deterministic
**v1.2.2** — bugfix: webhook adapter double-processing retried deliveries

**v1.3.0 — Metrics + packaging (target: 25 effort-weeks)**
- Prometheus exporter on configurable port
- Core metrics: events/sec, triggers fired/sec, queue depth, exec latency p50/p95/p99, MCP calls/sec (inbound + outbound, separately)
- Grafana dashboard JSON shipped in `examples/`
- Official multi-arch container image (amd64 + arm64) published to GHCR on release tags; Kubernetes example manifests under `examples/kubernetes/` (ADR-0011)

---

## Stable 2 band — `v2.0.0` → `v2.3.0`

> DAG executor; the storage query API and MCP client mode displaced from the old v1 band; the remaining source-adapter plugins and the notification plugins; speculative execution, multi-profile configs, and DAG optimizations. **LTS until 18 months post-release.**

**v2.0.0 — DAG Executor (target: 30 effort-weeks)**
- `DAGExecutor`: topological sort, parallel fan-out, critical-path identification
- `continue_on_fail` per-node flag
- TOML config supports `[[trigger.workflow.step]]` DAG graphs

**v2.0.1** — bugfix: cycle detection missing self-loops
**v2.0.2** — bugfix: parallel node cancellation leaving orphan subprocesses

**v2.1.0 — Storage API + MCP client mode (target: 33 effort-weeks)**
- Public `EventStore` query API
- `mcp_tool` Step kind; `MCPClientGateway` stable
- Regex and predicate-function `match` specs in `TriggerEngine`

*(Displaced from the old v1.1.0 when the v1 band was re-sequenced for the co-equal DevOps audience — ADR-0011.)*

**v2.2.0 — Source-adapter + notification plugins (target: 36 effort-weeks)**
- `watchflow-queue`, `watchflow-git`, `watchflow-ci` ship as official first-party source-adapter plugins (`watchflow-webhook` already shipped in v1.2.0)
- Notification plugins: `watchflow-slack`, `watchflow-github`, `watchflow-notify`
- Webhook/queue payload validation hardened per [`SECURITY_MODEL.md`](./SECURITY_MODEL.md)

**v2.3.0 — Speculative execution, multi-profile + DAG optimizations (target: 39 effort-weeks)**
- Per-trigger `speculative: true` flag
- Named profiles (`dev`, `ci`, `prod`); `watchflow run --profile ci`
- `watchflow-discord` plugin
- Result caching for deterministic nodes
- `watchflow dag show` visualizer command

**v2.3.1** — bugfix: profile env merge order

---

## Stable 3 band — `v3.0.0` → `v3.2.0`

> Full observability, OpenTelemetry, alerting, MCP Gateway hardening. Per-component health endpoints already shipped in v1.1.0; this band adds the correlated logs/metrics/traces layer on top. **LTS until 18 months post-release. Governance shifts to RFC-based Core Team — see [`DECISION_LOG.md`](./DECISION_LOG.md).**

**v3.0.0 — Full observability (target: 44 effort-weeks)**
- All async tasks named and tracked
- Logs/metrics/traces correlated by `trace_id`, `event_id`, and `mcp_origin` where applicable

**v3.0.1** — bugfix: trace ID propagation missing on plugin-emitted events

**v3.1.0 — OpenTelemetry (target: 47 effort-weeks)**
- `watchflow-otel` promoted to first-class; OTLP exporter compatible with Jaeger, Tempo, Honeycomb, Datadog

**v3.1.1** — bugfix: OTLP retry storm under broker downtime

**v3.2.0 — Alerting + MCP Gateway hardening (target: 50 effort-weeks)**
- Alert rules (`[alerts]`): failure rate, p95 latency, queue saturation
- `watchflow-pagerduty` plugin
- MCP Gateway: per-caller rate limiting, `requires_confirmation` flow shipped stable

---

## Stable 4 band — `v4.0.0` → `v4.2.0`

> Plugin platform, plugin distribution, LSP. The daemon and the embeddable `Engine` API shipped in the v1 band; this band builds the extended plugin ecosystem on top. **LTS until 18 months post-release.**

**v4.0.0 — Plugin platform (target: 55 effort-weeks)**
- Expanded hook lifecycle (15+ hooks, including MCP-specific hooks)

**v4.0.1** — bugfix: plugin permission denial messages misleading

**v4.1.0 — Plugin distribution (target: 58 effort-weeks)**
- `watchflow plugin install` from PyPI or git; plugin marketplace metadata format

**v4.2.0 — LSP integration (target: 61 effort-weeks)**
- Language Server Protocol surface for `watchflow.toml`: validation, autocomplete, go-to-definition for triggers
- VS Code extension as official client

---

## Stable 5 band — `v5.0.0` → `v5.2.0`

> TUI stable, enterprise, cloud sync, AI-assisted trigger inference. **LTS for 36 months — long-term anchor release.**

**v5.0.0 — TUI stable (target: 67 effort-weeks)**
- `watchflow tui` ships stable, supported, first-class; all 7 panels (Status, Stream, Trigger, Execute, DAG, Storage, Observe)
- Attach to a running daemon over the v1.1 IPC

**v5.0.1** — bugfix: TUI redraw flicker on tab switch
**v5.0.2** — bugfix: TUI clipboard integration on Linux Wayland

**v5.1.0 — Enterprise + cloud sync (target: 71 effort-weeks)**
- Multi-machine event aggregation; optional, encrypted, opt-in cloud sync of the EventStore
- Team dashboards; SSO via plugins

**v5.1.1** — bugfix: cloud sync conflict resolution for clock-skewed nodes

**v5.2.0 — AI-assisted trigger inference (target: 75 effort-weeks)**
- `watchflow-ai` plugin first-class: suggests new Triggers from observed event/Run history over MCP
- Fully local model option via `llama.cpp`; cloud option via configurable provider

---

## Version-band summary

| Band | Versions | Theme | LTS window |
|---|---|---|---|
| Pre-release | `v0.1.0` → `v0.3.2` | Bootstrap, EventBus, TriggerEngine, Scheduler, RC | none |
| Stable 1 | `v1.0.0` → `v1.3.0` | Core MVP, daemon + operability, plugin host + webhook, metrics + packaging | 18 months |
| Stable 2 | `v2.0.0` → `v2.3.0` | DAG, storage API + MCP client, source-adapter + notification plugins, speculative exec, multi-profile | 18 months |
| Stable 3 | `v3.0.0` → `v3.2.0` | Full observability, OpenTelemetry, alerting, MCP hardening | 18 months |
| Stable 4 | `v4.0.0` → `v4.2.0` | Plugin platform, plugin distribution, LSP | 18 months |
| Stable 5 | `v5.0.0` → `v5.2.0` | TUI stable, enterprise, cloud sync, AI trigger inference | 36 months |

---

## Release strategy

**Tag conventions:** stable `v1.0.0`; patch `v1.0.1`; pre-release `v0.3.2-rc1`. All tags signed (`git tag -s`).

**Release artifacts:** source tarball, universal Python wheel, standalone CLI binaries (`linux/macos/windows` × `x86_64/arm64`, from v1.0+), official multi-arch container image (`amd64 + arm64`) published to GHCR on release tags (from v1.3.0), `SHA256SUMS`, detached GPG signature, auto-generated release notes.

**Branch strategy:** `main` (protected, always releasable), `develop` (integration), `feature/*`, `release/vX.Y.Z`, `hotfix/*`.

**Patch cadence:** within 72 hours of a confirmed critical bug; non-critical patches batch into the next planned patch.
