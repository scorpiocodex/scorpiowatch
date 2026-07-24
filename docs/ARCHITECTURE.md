# Architecture

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index. Governed by [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md).*

---

## 1. System diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                             WATCHFLOW ENGINE                             │
│                                                                          │
│  Source Adapters                                                       │
│  core: filesystem · cron · manual · mcp-trigger                        │
│  plugin: webhook · queue · git · ci-provider                           │
│        │  Event                                                        │
│        ▼                                                                │
│  ┌──────────┐        ┌───────────────┐        ┌────────────┐           │
│  │ EventBus │ ─────▶ │ TriggerEngine │ ─────▶ │  Scheduler │           │
│  │ (bounded)│        │ match + score │        │rate/dedupe │           │
│  └──────────┘        └───────────────┘        └─────┬──────┘           │
│                                                       │ Run              │
│                                                       ▼                  │
│                                                ┌──────────────┐          │
│                                                │  DAGExecutor │◀── MCP   │
│                                                │   (Steps)    │  client  │
│                                                └──────┬───────┘  steps → external
│                                                       │ Result            MCP tools
│                                                       ▼                  │
│                                                ┌──────────────┐          │
│                                                │  EventStore  │          │
│                                                └──────────────┘          │
│                                                                          │
│  MCP Gateway (server mode) ◀── tool calls ── AI agents / MCP clients    │
│    exposes: trigger_workflow · get_run_status · query_event_history     │
│                                                                          │
│  ── observability sidecar (always-on): structlog · metrics · TUI ──    │
│  ── plugin host (cross-cutting): adapters · steps · exporters ──       │
└──────────────────────────────────────────────────────────────────────────┘
```

Every arrow is an `async` boundary. Every box is independently testable in isolation. No box other than the observability sidecar and the TUI knows the engine has a UI at all.

---

## 2. Layered view

WatchFlow is organized in four layers, each only permitted to depend on the layer below it (enforced in CI — see [`CODING_STANDARD.md`](./CODING_STANDARD.md)):

| Layer | Contains | Depends on |
|---|---|---|
| **Interface** | CLI, TUI, MCP server surface, embeddable `Engine` API | Core |
| **Extension** | Plugins: adapters, step kinds, exporters, MCP tool bindings | Core (via stable plugin API only) |
| **Core** | EventBus, TriggerEngine, Scheduler, DAGExecutor, EventStore, MCP Gateway | Adapter layer (via abstractions only) |
| **Adapter** | Platform- and protocol-specific I/O: `inotify`/`FSEvents`/`ReadDirectoryChangesW`, HTTP listener, MQ client, MCP transport | Nothing above |

The core never imports a concrete platform API directly — it depends on an abstract `SourceAdapter` interface, satisfied by whichever adapter is active. This is what makes cross-platform parity (Article II of the constitution) a structural property instead of a testing discipline.

---

## 3. End-to-end data flow — worked example

**Scenario:** a developer saves a `.py` file while an AI coding agent, connected over MCP, is also watching the same repository.

1. The **Filesystem Source Adapter** detects the write via the platform-native watcher and emits a normalized `Event{source="filesystem", type="modified", payload={path: "src/api.py"}}` onto the `EventBus`.
2. The `EventBus` delivers the event to all subscribers, including the `TriggerEngine`.
3. The `TriggerEngine` matches the event against declared `Trigger`s. The `run-tests` trigger's glob `**/*.py` matches; a confidence score is computed and clears the trigger's threshold.
4. The `Scheduler` checks the dedupe key and cooldown window, then admits a new `Run` of the `run-tests` Workflow.
5. The `DAGExecutor` runs the Workflow's `Step`s — here, a single `subprocess` step invoking `pytest`, launched via `asyncio.create_subprocess_exec` with `shell=False`.
6. The `Run`'s result (status, duration, captured output) is written to the `EventStore` in a batched, WAL-mode `aiosqlite` transaction.
7. Meanwhile, the AI agent calls `get_run_status` through the **MCP Gateway** (server mode) to check whether its own edit caused a test failure — using the same `EventStore` the human-facing TUI reads from, through the same observability bus.

No component in this chain needed to know whether its caller was a human, a cron tick, or an AI agent. That symmetry is the point of Article VII.

---

## 4. Cross-platform architecture

| Concern | Linux | macOS | Windows |
|---|---|---|---|
| Filesystem events | `inotify` | `FSEvents` | `ReadDirectoryChangesW` |
| Process spawn | `asyncio.create_subprocess_exec` | same | same (via ProactorEventLoop) |
| Path handling | POSIX paths, case-sensitive | POSIX paths, case-insensitive by default | `pathlib.PureWindowsPath` normalization at adapter boundary |
| Daemon mode | systemd unit | launchd plist | Windows Service wrapper |

Webhook, queue, cron, and MCP adapters are platform-neutral by construction — they never touch OS-specific file-watching APIs, so they need no per-platform code path at all.

---

## 5. Deployment topologies

WatchFlow is designed to run in four shapes without any code change to the core:

1. **Ephemeral CLI run (local dev)** — `watchflow run .` in a repo, foreground, attached to a terminal, `--dry-run` and `--once` available for tight iteration.
2. **Long-running daemon (DevOps / CI runner sidecar)** — `watchflow daemon`, systemd/launchd-managed, IPC over a Unix socket or named pipe, crash-only recovery backed by the `EventStore`.
3. **Embedded library** — `from watchflow import Engine` inside another Python process; the engine runs in-process with no CLI or daemon involved.
4. **MCP server process (AI-agent-facing)** — `watchflow mcp serve`, a long-lived process an AI agent's MCP client connects to, exposing a curated, permissioned subset of the engine's capabilities (see [`MCP_INTEGRATION.md`](./MCP_INTEGRATION.md)).

All four topologies share the same core engine binary and configuration format; they differ only in which Interface-layer entry point is used.

---

## 6. Concurrency model (summary)

Structured concurrency via `asyncio.TaskGroup`; every bounded queue specifies `maxsize`; cancellation propagates cleanly through the DAG. Full detail, including the Run lifecycle state machine and retry/idempotency semantics, lives in [`EXECUTION_MODEL.md`](./EXECUTION_MODEL.md).

---

## 7. Extensibility surface (summary)

Four extension points, each with a stable, versioned contract: Source Adapter, Step kind, Exporter, and MCP tool binding. Full contracts, hook lifecycles, and the permission model live in [`PLUGIN_SPECIFICATION.md`](./PLUGIN_SPECIFICATION.md) and [`MCP_INTEGRATION.md`](./MCP_INTEGRATION.md).

---

## 8. What lives where

For the literal repository directory layout that implements this architecture, see [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md). For the interface of each box in the diagram above, see [`MODULE_SPECIFICATIONS.md`](./MODULE_SPECIFICATIONS.md).
