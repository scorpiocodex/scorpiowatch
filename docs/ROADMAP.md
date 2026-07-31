# Roadmap

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index.*

Five stable majors, same discipline as the original filesystem-only plan, now carrying the broader scope of [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md). LTS is **18 months** for v1–v4, **36 months** for v5. Each band's final minor overlaps with the next major's release-candidate cycle.

---

## Planning basis — capacity, effort-weeks, and the calendar

> **Capacity assumption (maintainer-owned).** One maintainer, part-time, **~10 focused hours per week** alongside a full-time job.
> **Unit.** Targets are **cumulative effort-weeks from project start**, where **1 effort-week = 40 hours of focused work** (one full-time-equivalent week).
> **Conversion.** At ~10 h/week, 1 effort-week ≈ 4 calendar weeks ≈ **1 calendar month**. So "15 effort-weeks" means roughly **month 15 — about 1¼ years in — not fifteen weeks.** Every concrete target below is written in **both units** so this is unmissable.

The old targets on this roadmap were bare calendar months ("target: month 4"): set before ADR-0003 roughly doubled v1.0.0's scope, never revised, and meaningless without the capacity behind them. Effort-weeks fix that by measuring *work delivered* rather than time elapsed, so a slow month or a burst of free time re-times the calendar without invalidating the plan.

The ~10 h/week capacity is a **maintainer-owned assumption, not a measurement** — it should be revised as real throughput becomes known. Because every calendar equivalent is derived from it, **revising the capacity rescales every calendar figure at once** (20 h/week halves them; 5 h/week doubles them) while leaving the effort-week figures untouched. Revising the capacity, or any single target, **does not require an ADR** — only changing the capabilities in a band, or their sequencing across bands, does (see ADR-0011). LTS windows are the exception: they are commitments in calendar months from each release and are not effort-based.

---

## Pre-release band — `v0.1.0` → `v0.3.2`

> Bootstrap to release candidate. No public stability guarantees.

**v0.1.0 — Bootstrap (target: 4 effort-weeks ≈ month 4)**
- Project scaffolding per [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md); MIT license, base CI
- `FilesystemAdapter` (Linux only), `ManualAdapter`
- `TriggerEngine` with glob patterns, no scoring yet
- `Executor`: a linear (single-branch) Workflow of `subprocess` Steps, `shell=False`, per-step opt-in `timeout_s` (default none), process-group teardown, and streamed-and-bounded output capture ([`EXECUTION_MODEL.md`](./EXECUTION_MODEL.md) §2/§6)
- `Scheduler` **seam**: admits every fired Trigger's Workflow to the `Executor` and owns the `Run` lifecycle. **Cooldown was pulled forward here** from v0.3.0 — a leading-edge per-`(trigger, matched_path)` throttle, on by default, observable via `admission.suppressed` ([`MODULE_SPECIFICATIONS.md`](./MODULE_SPECIFICATIONS.md) §4). The rest of the full `Scheduler` (dedupe, rate limiting) remains v0.3.0.
- `watchflow run` and a **language-neutral** `watchflow init` (a portable demo trigger plus commented Python / JS-TS / Rust / Go / full-stack examples)

**v0.1.1** — bugfix: adapter not closing cleanly on SIGINT
**v0.1.2** — bugfix: subprocess zombies on cancellation

**v0.2.0 — EventBus + Storage skeleton (target: 7 effort-weeks ≈ month 7)**
- Bounded `EventBus` with backpressure strategies
- `EventStore` skeleton (`aiosqlite`, no batching yet)
- macOS `FilesystemAdapter` support
- `CronAdapter` (core-bundled)
- Basic confidence scoring in `TriggerEngine`
- `TriggerEngine` hardening: per-trigger error isolation in the `evaluate` loop — one trigger's evaluation failure is contained and logged, never sinking the batch ([`ENGINEERING_PRINCIPLES.md`](./ENGINEERING_PRINCIPLES.md) §7) — needed once non-glob (predicate) matching lands

**v0.2.1** — bugfix: queue overflow not raising `BackpressureError`
**v0.2.2** — bugfix: macOS `FSEvents` missing initial scan

**v0.3.0 — TriggerEngine + Scheduler (target: 10 effort-weeks ≈ month 10)**
- `Scheduler` — rate limiting and dedupe (cooldown already shipped in v0.1.0)
- **Debounce** — trailing-edge coalescing that waits out a spaced burst and runs once on the *settled* state. This is distinct from v0.1.0's leading-edge cooldown (run first, suppress the rest): debounce trades immediacy for running on the final state, and is the better fit for slow settle-then-run workflows.
- Windows `FilesystemAdapter` support (`ReadDirectoryChangesW`)
- `MCPTriggerAdapter` + minimal MCP server mode (`trigger_workflow` only)
- `watchflow check` and `watchflow doctor`

**v0.3.1** — bugfix: dedupe key collisions for similar paths
**v0.3.2** — release candidate for v1.0.0: doc freeze, API freeze, soak testing (target: 12 effort-weeks ≈ month 12)

---

## Stable 1 band — `v1.0.0` → `v1.3.0`

> First stable major: core MVP for local development, then the DevOps unlock — daemon, operability, and container packaging together; the plugin host carrying the webhook source adapter; then metrics. Serves both co-equal primary audiences within the first stable band (ADR-0011). **LTS until 18 months post-release.**

**v1.0.0 — Core MVP stable (target: 15 effort-weeks ≈ month 15)**
- Filesystem, cron, and manual adapters; linear (single-branch) Workflows; `TriggerEngine`; `Scheduler`; `Executor`; `EventStore`; CLI — fully usable for local development
- Public API freeze for `Engine`, `Workflow`, `Trigger`, `Step` (the embedded-library topology is usable from here)
- 80%+ test coverage; Linux/macOS/Windows wheels on PyPI
- Signed GitHub Release with SHA-256 checksums

**v1.0.1** — bugfix: race in `Scheduler.cooldown_check`
**v1.0.2** — bugfix: `EventStore` migration on first run

**v1.1.0 — Daemon, operability + container packaging (target: 21 effort-weeks ≈ month 21)**
- **Per-trigger environment / toolchain resolution (front of this band).** Today every Step inherits the one environment WatchFlow runs under; a Step whose command is itself a toolchain launcher (e.g. `uv run`, `npm`) resolves against **WatchFlow's** environment, not the watched project's — WatchFlow's own `VIRTUAL_ENV` / `UV_*` vars can leak into and mislead the child. v1.1 gives each Trigger its own resolved environment/toolchain so a child runs in the project's context. The **full-stack / polyglot** use case ([`WATCHFLOW.md`](./WATCHFLOW.md) §3) raises its priority: a Node front end and a Python back end in one repo need per-trigger toolchains, not one inherited environment.
- **Cooldown-map eviction under a long-lived daemon:** v0.1.0 uses lazy purge-on-access (a key touched once lingers until the next over-threshold admission). Revisit a periodic sweep now that the daemon runs for weeks (see [`MODULE_SPECIFICATIONS.md`](./MODULE_SPECIFICATIONS.md) §4).
- `watchflow daemon` (systemd/launchd-managed), Unix socket / named pipe IPC, crash-only recovery reconciled against the `EventStore`
- Per-component health endpoints (liveness/readiness), bind address configurable
- Graceful shutdown on `SIGTERM`/`SIGINT` with a bounded drain window (see [`EXECUTION_MODEL.md`](./EXECUTION_MODEL.md) §7)
- Process exit-code semantics for CI composition (see [`EXECUTION_MODEL.md`](./EXECUTION_MODEL.md) §7)
- Environment-variable configuration override (see [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md) §3)
- Official multi-arch container image (amd64 + arm64) published to GHCR on release tags; systemd and launchd units and one illustrative Kubernetes manifest shipped under `examples/devops-daemon/` (ADR-0011). Container packaging ships here, not later, because the daemon and its container image are one deliverable for most DevOps deployments, and the Kubernetes sidecar topology in [`ARCHITECTURE.md`](./ARCHITECTURE.md) §5 is otherwise unreachable until the metrics band.
- **This is the DevOps unlock.**

**v1.1.1** — bugfix: WAL checkpoint hang under sustained writes
**v1.1.2** — bugfix: daemon socket permissions on restart
**v1.1.3** — bugfix: health endpoint not respecting bind address

**v1.2.0 — Plugin host + webhook source adapter (target: 24 effort-weeks ≈ month 24)**
- `PluginHost` with entry-point discovery and capability grants
- Plugin sandboxing (no `subprocess`/`network` access without explicit grant)
- `watchflow-webhook` ships as the first official source-adapter plugin — the plugin host is pulled forward to here so the webhook adapter arrives **as a plugin, never promoted into core** (ADR-0006, ADR-0011)

*(Scope unchanged from before the container move; its target shifts from 22 to 24 effort-weeks only because the container-packaging work now lands ahead of it in v1.1.0.)*

**v1.2.1** — bugfix: plugin load order non-deterministic
**v1.2.2** — bugfix: webhook adapter double-processing retried deliveries

**v1.3.0 — Metrics (target: 25 effort-weeks ≈ month 25)**
- Prometheus exporter on configurable port
- Core metrics: events/sec, triggers fired/sec, queue depth, exec latency p50/p95/p99, MCP calls/sec (inbound + outbound, separately)
- Grafana dashboard JSON shipped in `examples/`

*(A light minor — just +1 effort-week over v1.2.0 — now that container packaging has moved to v1.1.0 and the observability bus already exists from the core.)*

---

## Stable 2 band — `v2.0.0` → `v2.3.0`

> DAG executor; the storage query API and MCP client mode displaced from the old v1 band; the remaining source-adapter plugins and the notification plugins; speculative execution, multi-profile configs, and DAG optimizations; Windows daemon parity. **LTS until 18 months post-release.**

**v2.0.0 — DAG Executor + Windows daemon parity (target: 30 effort-weeks ≈ month 30)**
- `DAGExecutor`: topological sort, parallel fan-out, critical-path identification
- `continue_on_fail` per-node flag
- TOML config supports `[[trigger.workflow.step]]` DAG graphs
- **Windows Service wrapper** for `watchflow daemon` — brings Windows to daemon parity; Linux (`systemd`) and macOS (`launchd`) daemon support shipped first in v1.1.0 (see [`ARCHITECTURE.md`](./ARCHITECTURE.md) §4)

**v2.0.1** — bugfix: cycle detection missing self-loops
**v2.0.2** — bugfix: parallel node cancellation leaving orphan subprocesses

**v2.1.0 — Storage API + MCP client mode (target: 33 effort-weeks ≈ month 33)**
- Public `EventStore` query API
- `mcp_tool` Step kind; `MCPClientGateway` stable
- Regex and predicate-function `match` specs in `TriggerEngine`

*(Displaced from the old v1.1.0 when the v1 band was re-sequenced for the co-equal DevOps audience — ADR-0011.)*

**v2.2.0 — Source-adapter + notification plugins (target: 36 effort-weeks ≈ month 36)**
- `watchflow-queue`, `watchflow-git`, `watchflow-ci` ship as official first-party source-adapter plugins (`watchflow-webhook` already shipped in v1.2.0)
- Notification plugins: `watchflow-slack`, `watchflow-github`, `watchflow-notify`
- Webhook/queue payload validation hardened per [`SECURITY_MODEL.md`](./SECURITY_MODEL.md)

**v2.3.0 — Speculative execution, multi-profile + DAG optimizations (target: 39 effort-weeks ≈ month 39)**
- Per-trigger `speculative: true` flag
- Named profiles (`dev`, `ci`, `prod`); `watchflow run --profile ci`
- `watchflow-discord` plugin
- Result caching for deterministic nodes
- `watchflow dag show` visualizer command

**v2.3.1** — bugfix: profile env merge order

---

## Stable 3 band — `v3.0.0` → `v3.2.0`

> Full observability, OpenTelemetry, alerting, MCP Gateway hardening. Per-component health endpoints already shipped in v1.1.0; this band adds the correlated logs/metrics/traces layer on top. **LTS until 18 months post-release. Governance shifts to RFC-based Core Team — see [`DECISION_LOG.md`](./DECISION_LOG.md).**

> **Targets from here on are directional, not scheduled.** Concrete effort-week targets run through the end of the v2 band (v2.3.0 ≈ month 39). At the stated capacity, v5.2.0 would land beyond five years out — far enough that a month-precise number would be false precision. From v3.0.0 onward the bands are therefore ordered by **theme and dependency order, not by date**; numeric targets will be set when the v2 band completes and real throughput is known. Scope, deliverables, patch releases, and LTS windows for these bands are fixed as written — only the target figures are deferred.

**v3.0.0 — Full observability (target: post-v2 — sequenced, not scheduled)**
- All async tasks named and tracked
- Logs/metrics/traces correlated by `trace_id`, `event_id`, and `mcp_origin` where applicable

**v3.0.1** — bugfix: trace ID propagation missing on plugin-emitted events

**v3.1.0 — OpenTelemetry (target: post-v2 — sequenced, not scheduled)**
- `watchflow-otel` promoted to first-class; OTLP exporter compatible with Jaeger, Tempo, Honeycomb, Datadog

**v3.1.1** — bugfix: OTLP retry storm under broker downtime

**v3.2.0 — Alerting + MCP Gateway hardening (target: post-v2 — sequenced, not scheduled)**
- Alert rules (`[alerts]`): failure rate, p95 latency, queue saturation
- `watchflow-pagerduty` plugin
- MCP Gateway: per-caller rate limiting, `requires_confirmation` flow shipped stable

---

## Stable 4 band — `v4.0.0` → `v4.2.0`

> Plugin platform, plugin distribution, LSP. The daemon and the embeddable `Engine` API shipped in the v1 band; this band builds the extended plugin ecosystem on top. **LTS until 18 months post-release.**

**v4.0.0 — Plugin platform (target: post-v2 — sequenced, not scheduled)**
- Expanded hook lifecycle (15+ hooks, including MCP-specific hooks)

**v4.0.1** — bugfix: plugin permission denial messages misleading

**v4.1.0 — Plugin distribution (target: post-v2 — sequenced, not scheduled)**
- `watchflow plugin install` from PyPI or git; plugin marketplace metadata format

**v4.2.0 — LSP integration (target: post-v2 — sequenced, not scheduled)**
- Language Server Protocol surface for `watchflow.toml`: validation, autocomplete, go-to-definition for triggers
- VS Code extension as official client

---

## Stable 5 band — `v5.0.0` → `v5.2.0`

> TUI stable, enterprise, cloud sync, AI-assisted trigger inference. **LTS for 36 months — long-term anchor release.**

**v5.0.0 — TUI stable (target: post-v2 — sequenced, not scheduled)**
- `watchflow tui` ships stable, supported, first-class; all 7 panels (Status, Stream, Trigger, Execute, DAG, Storage, Observe)
- Attach to a running daemon over the v1.1 IPC

**v5.0.1** — bugfix: TUI redraw flicker on tab switch
**v5.0.2** — bugfix: TUI clipboard integration on Linux Wayland

**v5.1.0 — Enterprise + cloud sync (target: post-v2 — sequenced, not scheduled)**
- Multi-machine event aggregation; optional, encrypted, opt-in cloud sync of the EventStore
- Team dashboards; SSO via plugins

**v5.1.1** — bugfix: cloud sync conflict resolution for clock-skewed nodes

**v5.2.0 — AI-assisted trigger inference (target: post-v2 — sequenced, not scheduled)**
- `watchflow-ai` plugin first-class: suggests new Triggers from observed event/Run history over MCP
- Fully local model option via `llama.cpp`; cloud option via configurable provider

---

## Version-band summary

| Band | Versions | Theme | LTS window |
|---|---|---|---|
| Pre-release | `v0.1.0` → `v0.3.2` | Bootstrap, EventBus, TriggerEngine, Scheduler, RC | none |
| Stable 1 | `v1.0.0` → `v1.3.0` | Core MVP, daemon + operability + packaging, plugin host + webhook, metrics | 18 months |
| Stable 2 | `v2.0.0` → `v2.3.0` | DAG + Windows daemon parity, storage API + MCP client, source-adapter + notification plugins, speculative exec, multi-profile | 18 months |
| Stable 3 | `v3.0.0` → `v3.2.0` | Full observability, OpenTelemetry, alerting, MCP hardening | 18 months |
| Stable 4 | `v4.0.0` → `v4.2.0` | Plugin platform, plugin distribution, LSP | 18 months |
| Stable 5 | `v5.0.0` → `v5.2.0` | TUI stable, enterprise, cloud sync, AI trigger inference | 36 months |

Concrete effort-week targets run through the v2 band; v3.0.0 onward is sequenced by theme and dependency order, not scheduled (see the Stable 3 band note).

---

## Release strategy

**Tag conventions:** stable `v1.0.0`; patch `v1.0.1`; pre-release `v0.3.2-rc1`. All tags signed (`git tag -s`).

**Release artifacts:** source tarball, universal Python wheel, standalone CLI binaries (`linux/macos/windows` × `x86_64/arm64`, from v1.0+), official multi-arch container image (`amd64 + arm64`) published to GHCR on release tags (from v1.1.0), `SHA256SUMS`, detached GPG signature, auto-generated release notes.

**Branch strategy:** `main` (protected, always releasable), `develop` (integration), `feature/*`, `release/vX.Y.Z`, `hotfix/*`.

**Patch cadence:** within 72 hours of a confirmed critical bug; non-critical patches batch into the next planned patch.
