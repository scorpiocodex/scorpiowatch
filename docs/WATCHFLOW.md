# WatchFlow — Event-Driven Workflow Orchestration Engine

> React to anything. Orchestrate everything. From your laptop, to your CI pipeline, to your AI agents.

A cross-platform, event-driven workflow orchestration engine for **local development**, **CI/CD**, **DevOps automation**, and **MCP-powered AI systems** — async-native top to bottom, safe by default, and small enough to read in an afternoon.

---

## Table of contents

1. Project identity
2. What changed, and why
3. What WatchFlow does
4. Core concepts (glossary)
5. Architecture at a glance
6. Who it's for
7. Documentation map
8. Quickstart
9. Status

---

## 1. Project identity

| Field | Value |
|---|---|
| **Name** | WatchFlow |
| **Tagline** | Cross-platform, event-driven workflow orchestration for local dev, CI/CD, DevOps automation, and MCP-powered AI systems |
| **Type** | Open-source Python orchestration engine — CLI, embeddable library, MCP server/client, optional TUI |
| **Author** | San Shibu (`ScorpioCodeX`) |
| **License** | MIT |
| **Language** | Python 3.12+ |
| **Repository** | `github.com/scorpiocodex/watchflow` |
| **Package** | `pip install watchflow` (extras: `[tui]`, `[otel]`, `[webhooks]`, `[queue]`, `[git]`, `[ci]`, `[mcp]`, `[all]`) |
| **Status** | Pre-release planning — scope just broadened, see [`ROADMAP.md`](./ROADMAP.md) |

---

## 2. What changed, and why

WatchFlow began as a filesystem-only reactive automation engine: watch a directory, react to file changes, run a command. That version is still the seed of everything here — the async-first, safe-execution, event-driven discipline carries forward unchanged.

What's changed is the boundary of "event." A file save is one kind of event. A cron tick, a webhook from a CI provider, a message on a queue, a git push, and — increasingly — a tool call from an AI agent over **MCP (Model Context Protocol)** are all the same shape of problem: *something happened, and the right work should run, safely, exactly once, with a durable record of what happened.*

WatchFlow v1+ generalizes around that shape. It is no longer just a file watcher — it's an **event-driven workflow orchestration engine** that happens to ship with a filesystem adapter as one of several first-class event sources. The full reasoning behind this pivot, including the terminology changes it forced (`Intent` → `Trigger`, `Pipeline` → `Workflow`), is recorded as ADR-0003 in [`DECISION_LOG.md`](./DECISION_LOG.md).

---

## 3. What WatchFlow does

WatchFlow sits between *"something happened"* and *"the right work ran, safely, with a record of it"* — across four domains:

- **Local development** — rerun tests, linters, type checkers, and bundlers the instant a file changes, without polling, duplicate runs, or shell-injection risk.
- **CI/CD** — a lightweight, self-hosted alternative or companion to heavyweight cloud pipelines, with sub-second reaction latency instead of seconds-to-minutes.
- **DevOps automation** — react to config rewrites, certificate renewals, queue messages, and webhooks by running health checks, reloads, or notifications, as a long-lived, daemonizable service.
- **MCP-powered AI systems** — act as the safe local execution substrate an AI agent reaches for instead of shelling out directly: an agent calls an MCP tool, WatchFlow runs the workflow with full dedupe, audit trail, and no `shell=True` anywhere in the path.

The lifecycle of a single event, regardless of source:

```
event (fs write · cron tick · webhook · queue message · git push · MCP tool call · manual)
   → Source Adapter (normalizes to a common Event envelope)
   → EventBus (bounded, backpressure-aware pub/sub)
   → TriggerEngine (pattern/condition matching + confidence scoring)
   → Scheduler (rate-limit + dedupe + cooldown)
   → DAGExecutor (strict subprocess / plugin / http / mcp_tool steps, shell=False always)
   → EventStore (aiosqlite batched writes — durable Run + Event history)
   → Observability (structlog + metrics + optional TUI)
```

Every stage is `async`. Every queue is bounded. Every subprocess uses `asyncio.create_subprocess_exec` with `shell=False`. No `to_thread`, no polling, no silent drops.

---

## 4. Core concepts (glossary)

| Term | Meaning |
|---|---|
| **Event** | A normalized envelope (`source`, `type`, `payload`, `timestamp`, `metadata`) emitted onto the EventBus by a Source Adapter |
| **Source Adapter** | Pluggable component that turns something happening in the world into an Event (filesystem, cron, webhook, queue, git, CI, MCP, manual) |
| **Trigger** | A declared rule: a match condition (glob, cron expression, payload predicate, MCP tool name) plus a target Workflow. Replaces the legacy term "Intent" |
| **Workflow** | A named DAG of Steps. Replaces the legacy term "Pipeline"; a linear pipeline is just a single-branch Workflow |
| **Step** | The atomic unit of execution inside a Workflow: `subprocess`, `plugin`, `http`, or `mcp_tool` |
| **Run** | One execution instance of a Workflow, with a full lifecycle (`pending → running → succeeded/failed/skipped/cancelled`) |
| **MCP Gateway** | The dual-mode integration layer: **server mode** exposes WatchFlow's own actions to AI agents as MCP tools; **client mode** lets a Workflow Step call an external MCP tool |

Full definitions and invariants live in [`MODULE_SPECIFICATIONS.md`](./MODULE_SPECIFICATIONS.md).

---

## 5. Architecture at a glance

```
┌────────────────────────────────────────────────────────────────────────┐
│                            WATCHFLOW ENGINE                            │
│                                                                        │
│  Source Adapters                                                      │
│  core: filesystem · cron · manual · mcp-trigger                       │
│  plugin: webhook · queue · git · ci-provider                          │
│       │  Event                                                        │
│       ▼                                                                │
│  ┌──────────┐      ┌───────────────┐      ┌────────────┐              │
│  │ EventBus │ ───▶ │ TriggerEngine │ ───▶ │  Scheduler │              │
│  │ (bounded)│      │ match + score │      │ rate/dedupe│              │
│  └──────────┘      └───────────────┘      └─────┬──────┘              │
│                                                   │ Run                │
│                                                   ▼                    │
│                                            ┌──────────────┐  MCP client│
│                                            │  DAGExecutor │◀─ step ────┼─▶ external
│                                            │   (Steps)    │  calls out │   MCP tools
│                                            └──────┬───────┘            │
│                                                   │ Result             │
│                                                   ▼                    │
│                                            ┌──────────────┐            │
│                                            │  EventStore  │            │
│                                            └──────────────┘            │
│                                                                        │
│  MCP Gateway (server mode) ◀── tool calls ── AI agents / MCP clients   │
│    exposes: trigger_workflow · get_run_status · query_event_history    │
│                                                                        │
│  ── observability sidecar (always-on): structlog · metrics · TUI ──   │
└────────────────────────────────────────────────────────────────────────┘
```

Full architecture, deployment topologies, and cross-platform detail: [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## 6. Who it's for

| Audience | Why they care |
|---|---|
| **Developers** | A typed, scriptable, `shell=True`-free replacement for `entr` / `watchexec` / `nodemon` |
| **DevOps & SRE** | A daemon-capable, observable event reactor for webhooks, queues, and config drift — fits systemd, Docker, Kubernetes sidecars |
| **Platform / CI engineers** | Sub-second local reaction loops that complement (not replace) cloud CI/CD |
| **AI agent builders** | A safe, audited execution substrate reachable over MCP — agents trigger Workflows instead of shelling out |
| **Tool builders** | An embeddable engine (`from watchflow import Engine`) for products that need their own reactive layer |
| **Educators / learners** | A small, explicit, async-pure reference architecture |

---

## 7. Documentation map

This README is the front door. Everything else lives in its own focused document:

| Document | Covers |
|---|---|
| [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md) | Mission, problem statement, non-negotiable tenets, non-goals, amendment process |
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | Layered system architecture, data flow, deployment topologies, cross-platform design |
| [`ENGINEERING_PRINCIPLES.md`](./ENGINEERING_PRINCIPLES.md) | How the constitution's values translate into daily engineering decisions |
| [`CODING_STANDARD.md`](./CODING_STANDARD.md) | Language/tooling, style, typing, testing, commit conventions, forbidden patterns |
| [`SECURITY_MODEL.md`](./SECURITY_MODEL.md) | Threat model, trust boundaries, subprocess/plugin/MCP safety, disclosure process |
| [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md) | Repository layout, module boundaries, config/state locations |
| [`MODULE_SPECIFICATIONS.md`](./MODULE_SPECIFICATIONS.md) | Per-module interfaces, invariants, and contracts |
| [`EXECUTION_MODEL.md`](./EXECUTION_MODEL.md) | Run lifecycle, DAG semantics, concurrency, retries, execution contexts |
| [`PLUGIN_SPECIFICATION.md`](./PLUGIN_SPECIFICATION.md) | Plugin types, hook contract, permissions, publishing, official plugin catalog |
| [`MCP_INTEGRATION.md`](./MCP_INTEGRATION.md) | WatchFlow as MCP server and client, provenance, AI-safety controls |
| [`UI_DESIGN.md`](./UI_DESIGN.md) | Shared design system, TUI panel-by-panel spec, CLI command reference and output conventions |
| [`ROADMAP.md`](./ROADMAP.md) | Full version roadmap, release strategy, LTS bands |
| [`DECISION_LOG.md`](./DECISION_LOG.md) | Architectural Decision Records — the "why" behind every major choice |

---

## 8. Quickstart

```bash
pip install watchflow[all]
watchflow init                 # scaffold watchflow.toml
watchflow run .                # start the engine, headless
```

Minimal config — a file-change Trigger and an MCP-callable Workflow:

```toml
[[trigger]]
name = "run-tests"
source = "filesystem"
patterns = ["**/*.py"]
threshold = 0.50

  [trigger.workflow]
  steps = [
    { kind = "subprocess", command = ["pytest", "-x", "--tb=short"], timeout_s = 30 },
  ]

[mcp.server]
enabled = true
expose = ["trigger_workflow", "get_run_status"]
```

An AI agent connected over MCP can now call `trigger_workflow("run-tests")` directly — no shell access required.

---

## 9. Status

Pre-release planning. Scope and terminology were broadened on **2026-07-07** (see ADR-0003). Implementation follows the six-phase build order and version bands in [`ROADMAP.md`](./ROADMAP.md).

— `ScorpioCodeX`
