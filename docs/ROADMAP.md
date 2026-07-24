# Roadmap

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index.*

Five stable majors, same discipline as the original filesystem-only plan, now carrying the broader scope of [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md). LTS is **18 months** for v1–v4, **36 months** for v5. Each band's final minor overlaps with the next major's release-candidate cycle.

---

## Pre-release band — `v0.1.0` → `v0.3.2`

> Bootstrap to release candidate. No public stability guarantees.

**v0.1.0 — Bootstrap (target: week 4)**
- Project scaffolding per [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md); MIT license, base CI
- `FilesystemAdapter` (Linux only), `ManualAdapter`
- `TriggerEngine` with glob patterns, no scoring yet
- `Executor` with single subprocess, `shell=False`, no timeout
- `watchflow run` and `watchflow init`

**v0.1.1** — bugfix: adapter not closing cleanly on SIGINT
**v0.1.2** — bugfix: subprocess zombies on cancellation

**v0.2.0 — EventBus + Storage skeleton (target: week 8)**
- Bounded `EventBus` with backpressure strategies
- `EventStore` skeleton (`aiosqlite`, no batching yet)
- macOS `FilesystemAdapter` support
- `CronAdapter` (core-bundled)
- Basic confidence scoring in `TriggerEngine`

**v0.2.1** — bugfix: queue overflow not raising `BackpressureError`
**v0.2.2** — bugfix: macOS `FSEvents` missing initial scan

**v0.3.0 — TriggerEngine + Scheduler (target: week 11)**
- `Scheduler` (rate limit, dedupe, cooldown)
- Windows `FilesystemAdapter` support (`ReadDirectoryChangesW`)
- `MCPTriggerAdapter` + minimal MCP server mode (`trigger_workflow` only)
- `watchflow check` and `watchflow doctor`

**v0.3.1** — bugfix: dedupe key collisions for similar paths
**v0.3.2** — release candidate for v1.0.0: doc freeze, API freeze, soak testing

---

## Stable 1 band — `v1.0.0` → `v1.3.0`

> First stable major: core MVP, storage API, plugin foundation, metrics, MCP client mode. **LTS until 18 months post-release.**

**v1.0.0 — Core MVP stable (target: month 4)**
- Public API freeze for `Engine`, `Workflow`, `Trigger`, `Step`
- 80%+ test coverage; Linux/macOS/Windows wheels on PyPI
- Signed GitHub Release with SHA-256 checksums

**v1.0.1** — bugfix: race in `Scheduler.cooldown_check`
**v1.0.2** — bugfix: `EventStore` migration on first run

**v1.1.0 — Storage API + MCP client mode (target: month 5)**
- Public `EventStore` query API
- `mcp_tool` Step kind; `MCPClientGateway` stable
- Regex and predicate-function `match` specs in `TriggerEngine`

**v1.1.1** — bugfix: WAL checkpoint hang under sustained writes

**v1.2.0 — Plugin foundation (target: month 6)**
- `PluginHost` with entry-point discovery and capability grants
- Official plugins: `watchflow-slack`, `watchflow-github`, `watchflow-notify`
- Plugin sandboxing (no `subprocess`/`network` access without explicit grant)

**v1.2.1** — bugfix: plugin load order non-deterministic

**v1.3.0 — Metrics layer (target: month 7)**
- Prometheus exporter on configurable port
- Core metrics: events/sec, triggers fired/sec, queue depth, exec latency p50/p95/p99, MCP calls/sec (inbound + outbound, separately)
- Grafana dashboard JSON shipped in `examples/`

---

## Stable 2 band — `v2.0.0` → `v2.3.0`

> DAG executor, first-party source-adapter plugins, speculative execution, multi-profile configs. **LTS until 18 months post-release.**

**v2.0.0 — DAG Executor (target: month 10)**
- `DAGExecutor`: topological sort, parallel fan-out, critical-path identification
- `continue_on_fail` per-node flag
- TOML config supports `[[trigger.workflow.step]]` DAG graphs

**v2.0.1** — bugfix: cycle detection missing self-loops
**v2.0.2** — bugfix: parallel node cancellation leaving orphan subprocesses

**v2.1.0 — Official source-adapter plugins (target: month 11)**
- `watchflow-webhook`, `watchflow-queue`, `watchflow-git`, `watchflow-ci` ship as official first-party plugins
- Webhook payload validation hardened per [`SECURITY_MODEL.md`](./SECURITY_MODEL.md)

**v2.1.1** — bugfix: webhook adapter double-processing retried deliveries

**v2.2.0 — Speculative execution + multi-profile (target: month 13)**
- Per-trigger `speculative: true` flag
- Named profiles (`dev`, `ci`, `prod`); `watchflow run --profile ci`
- `watchflow-discord` plugin

**v2.2.1** — bugfix: profile env merge order

**v2.3.0 — DAG optimizations (target: month 14)**
- Result caching for deterministic nodes
- `watchflow dag show` visualizer command

---

## Stable 3 band — `v3.0.0` → `v3.2.0`

> Full observability, OpenTelemetry, alerting, MCP Gateway hardening. **LTS until 18 months post-release. Governance shifts to RFC-based Core Team — see [`DECISION_LOG.md`](./DECISION_LOG.md).**

**v3.0.0 — Full observability (target: month 18)**
- All async tasks named and tracked; per-component health endpoints
- Logs/metrics/traces correlated by `trace_id`, `event_id`, and `mcp_origin` where applicable

**v3.0.1** — bugfix: trace ID propagation missing on plugin-emitted events
**v3.0.2** — bugfix: health endpoint not respecting bind address

**v3.1.0 — OpenTelemetry (target: month 20)**
- `watchflow-otel` promoted to first-class; OTLP exporter compatible with Jaeger, Tempo, Honeycomb, Datadog

**v3.1.1** — bugfix: OTLP retry storm under broker downtime

**v3.2.0 — Alerting + MCP Gateway hardening (target: month 21)**
- Alert rules (`[alerts]`): failure rate, p95 latency, queue saturation
- `watchflow-pagerduty` plugin
- MCP Gateway: per-caller rate limiting, `requires_confirmation` flow shipped stable

---

## Stable 4 band — `v4.0.0` → `v4.2.0`

> Plugin platform, daemon, embedded API, LSP. **LTS until 18 months post-release.**

**v4.0.0 — Plugin platform (target: month 26)**
- Expanded hook lifecycle (15+ hooks, including MCP-specific hooks)
- `watchflow plugin install` from PyPI or git; plugin marketplace metadata format

**v4.0.1** — bugfix: plugin permission denial messages misleading

**v4.1.0 — Daemon + Embedded API (target: month 28)**
- `watchflow daemon` (systemd/launchd-friendly), Unix socket / named pipe IPC
- `Engine` promoted to fully public embeddable API

**v4.1.1** — bugfix: daemon socket permissions on restart

**v4.2.0 — LSP integration (target: month 30)**
- Language Server Protocol surface for `watchflow.toml`: validation, autocomplete, go-to-definition for triggers
- VS Code extension as official client

---

## Stable 5 band — `v5.0.0` → `v5.2.0`

> TUI stable, enterprise, cloud sync, AI-assisted trigger inference. **LTS for 36 months — long-term anchor release.**

**v5.0.0 — TUI stable (target: month 36)**
- `watchflow tui` ships stable, supported, first-class; all 7 panels (Status, Stream, Trigger, Execute, DAG, Storage, Observe)
- Attach to a running daemon over the v4.1 IPC

**v5.0.1** — bugfix: TUI redraw flicker on tab switch
**v5.0.2** — bugfix: TUI clipboard integration on Linux Wayland

**v5.1.0 — Enterprise + cloud sync (target: month 39)**
- Multi-machine event aggregation; optional, encrypted, opt-in cloud sync of the EventStore
- Team dashboards; SSO via plugins

**v5.1.1** — bugfix: cloud sync conflict resolution for clock-skewed nodes

**v5.2.0 — AI-assisted trigger inference (target: month 42)**
- `watchflow-ai` plugin first-class: suggests new Triggers from observed event/Run history over MCP
- Fully local model option via `llama.cpp`; cloud option via configurable provider

---

## Version-band summary

| Band | Versions | Theme | LTS window |
|---|---|---|---|
| Pre-release | `v0.1.0` → `v0.3.2` | Bootstrap, EventBus, TriggerEngine, Scheduler, RC | none |
| Stable 1 | `v1.0.0` → `v1.3.0` | Core MVP, Storage API, Plugins, MCP client, Metrics | 18 months |
| Stable 2 | `v2.0.0` → `v2.3.0` | DAG, official adapter plugins, speculative exec, multi-profile | 18 months |
| Stable 3 | `v3.0.0` → `v3.2.0` | Full observability, OpenTelemetry, alerting, MCP hardening | 18 months |
| Stable 4 | `v4.0.0` → `v4.2.0` | Plugin platform, daemon, embedded API, LSP | 18 months |
| Stable 5 | `v5.0.0` → `v5.2.0` | TUI stable, enterprise, cloud sync, AI trigger inference | 36 months |

---

## Release strategy

**Tag conventions:** stable `v1.0.0`; patch `v1.0.1`; pre-release `v0.3.2-rc1`. All tags signed (`git tag -s`).

**Release artifacts:** source tarball, universal Python wheel, standalone CLI binaries (`linux/macos/windows` × `x86_64/arm64`, from v1.0+), `SHA256SUMS`, detached GPG signature, auto-generated release notes.

**Branch strategy:** `main` (protected, always releasable), `develop` (integration), `feature/*`, `release/vX.Y.Z`, `hotfix/*`.

**Patch cadence:** within 72 hours of a confirmed critical bug; non-critical patches batch into the next planned patch.
