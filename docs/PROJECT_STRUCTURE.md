# Project Structure

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index. Implements the layering defined in [`ARCHITECTURE.md`](./ARCHITECTURE.md).*

---

## 1. Repository layout

```
watchflow/
├── src/
│   └── watchflow/
│       ├── core/                  # Layer: Core — no concrete platform/plugin imports
│       │   ├── events.py          # Event envelope, EventBus
│       │   ├── triggers.py        # Trigger model, TriggerEngine
│       │   ├── scheduler.py       # Rate-limit, dedupe, cooldown, speculative exec
│       │   ├── workflow.py        # Workflow, Step models
│       │   └── engine.py          # Engine — the public embeddable entry point
│       │
│       ├── execution/              # Layer: Core — DAG + step execution
│       │   ├── executor.py        # Single-step execution, subprocess safety
│       │   └── dag.py             # DAGExecutor: topo sort, fan-out, critical path
│       │
│       ├── storage/                 # Layer: Core — durable EventStore
│       │   ├── event_store.py
│       │   └── migrations/
│       │
│       ├── mcp/                     # Layer: Core — MCP Gateway (server + client)
│       │   ├── server.py          # WatchFlow-as-MCP-server tool/resource surface
│       │   ├── client.py          # WatchFlow-as-MCP-client step binding
│       │   └── gateway.py         # Shared provenance, schema validation, rate limits
│       │
│       ├── observability/           # Layer: Core — always-on sidecar
│       │   ├── logging.py         # structlog configuration, redaction processors
│       │   ├── metrics.py         # Prometheus exporter
│       │   └── tracing.py         # OpenTelemetry exporter
│       │
│       ├── adapters/                 # Layer: Adapter — platform/protocol specific
│       │   ├── base.py            # Abstract SourceAdapter Protocol
│       │   ├── filesystem.py      # core-bundled
│       │   ├── cron.py            # core-bundled
│       │   ├── manual.py          # core-bundled
│       │   └── mcp_trigger.py     # core-bundled
│       │
│       ├── plugins/                  # Layer: Extension — official first-party plugins
│       │   ├── host.py            # Plugin discovery, lifecycle, permission grants
│       │   ├── webhook/           # optional extra: [webhooks]
│       │   ├── queue/             # optional extra: [queue]
│       │   ├── git/               # optional extra: [git]
│       │   ├── ci/                # optional extra: [ci]
│       │   ├── slack/
│       │   ├── github/
│       │   └── notify/
│       │
│       ├── cli/                      # Layer: Interface
│       │   ├── main.py            # typer app entry point
│       │   └── commands/
│       │
│       ├── tui/                      # Layer: Interface — optional extra: [tui]
│       │   └── app.py              # textual application
│       │
│       └── config/                   # Config loading + validation (used by all layers)
│           ├── schema.py          # pydantic models for watchflow.toml
│           └── loader.py
│
├── tests/                          # Mirrors src/watchflow/ 1:1 (see §2)
│   ├── core/
│   ├── execution/
│   ├── storage/
│   │   └── migrations/
│   ├── mcp/
│   ├── observability/
│   ├── adapters/
│   ├── plugins/
│   │   ├── webhook/
│   │   ├── queue/
│   │   ├── git/
│   │   ├── ci/
│   │   ├── slack/
│   │   ├── github/
│   │   └── notify/
│   ├── cli/
│   │   └── commands/
│   ├── tui/
│   ├── config/
│   └── cross_platform/            # deliberate addition — no source counterpart (Linux/macOS/Windows adapter matrix)
│
├── examples/                       # Example watchflow.toml configs, one per use case
│   ├── local-dev/
│   ├── ci-cd/
│   ├── devops-daemon/          # daemon config + systemd/launchd units + container/K8s example (ADR-0011)
│   │   └── kubernetes/         # illustrative K8s Deployment manifest with liveness/readiness probes
│   └── mcp-agent/
│
├── docs/                            # This documentation set
│   ├── WATCHFLOW.md
│   ├── PROJECT_CONSTITUTION.md
│   ├── ARCHITECTURE.md
│   ├── ENGINEERING_PRINCIPLES.md
│   ├── CODING_STANDARD.md
│   ├── SECURITY_MODEL.md
│   ├── PROJECT_STRUCTURE.md
│   ├── MODULE_SPECIFICATIONS.md
│   ├── EXECUTION_MODEL.md
│   ├── PLUGIN_SPECIFICATION.md
│   ├── MCP_INTEGRATION.md
│   ├── UI_DESIGN.md
│   ├── watchflow_terminal_mockup_7_views.html   # TUI reference implementation (see UI_DESIGN.md §3.1)
│   ├── ROADMAP.md
│   └── DECISION_LOG.md
│
├── rfcs/                            # RFC process, active from Core Team governance stage
│
├── Dockerfile                       # Official multi-arch image (GHCR, amd64+arm64), built on release tags — ADR-0011
├── pyproject.toml
├── SECURITY.md
└── LICENSE
```

The `tests/` directories are created as their source counterparts are implemented, so the tree describes the target layout, not the state at any given version.

---

## 2. Module boundary rules

Enforced by an import-linter contract in CI (see [`CODING_STANDARD.md`](./CODING_STANDARD.md)):

- `core/`, `execution/`, `storage/`, `mcp/`, and `observability/` may depend on each other and on `adapters/base.py` (the abstract `Protocol` only), and on `config/`.
- `adapters/*` (concrete implementations) may depend on `core/` abstractions but never the reverse.
- `plugins/*` may depend on the stable plugin API surface only (re-exported from `core/`), never on internal core modules directly.
- `cli/` and `tui/` may depend on everything below them, but nothing below depends on either.
- `tests/` mirrors `src/watchflow/` exactly, one test module per source module minimum.

---

## 3. Config and state locations

| Path | Purpose |
|---|---|
| `watchflow.toml` | Project-root configuration: Triggers, Workflows, adapters, MCP settings |
| `.watchflow/events.db` | `aiosqlite`-backed EventStore (WAL mode) |
| `.watchflow/plugins.lock` | Resolved plugin versions and granted capabilities |
| `~/.config/watchflow/` | User-level defaults, applied before project config |
| `.watchflow/daemon.sock` | Unix socket for daemon-mode IPC (named pipe on Windows) |

### Environment-variable configuration

A container should not have to bake a `watchflow.toml` into its image to be configured, so every value in the `pydantic` config schema (`config/schema.py`) is also settable through the environment.

- **Naming convention:** `WATCHFLOW_` prefix, uppercased key, nested sections joined by a double underscore `__`. Examples: `WATCHFLOW_SCHEDULER__MAX_PARALLEL=8`, `WATCHFLOW_MCP__SERVER__ENABLED=true`, `WATCHFLOW_DAEMON__SHUTDOWN_GRACE_S=45`. Values are parsed and validated against the same schema as their TOML equivalents — an invalid environment value fails fast with the same field-level error a bad `watchflow.toml` would (see [`MODULE_SPECIFICATIONS.md`](./MODULE_SPECIFICATIONS.md) §10).
- **Precedence (highest wins):**
  1. Explicit CLI flags
  2. Environment variables (`WATCHFLOW_*`)
  3. Project `watchflow.toml`
  4. User-level defaults (`~/.config/watchflow/`)
  5. Built-in schema defaults

Environment variables sit *above* both file layers precisely so a container or systemd unit can override any file-provided setting without editing a mounted file, while a developer's explicit CLI flag still wins over an inherited environment.

---

## 4. Naming conventions for files

- One class per file for core abstractions (`triggers.py` defines `Trigger` and `TriggerEngine` together only because they're a tightly coupled pair — otherwise, split).
- Adapter files are named after the event source they wrap (`filesystem.py`, not `fs_watcher_impl.py`).
- Plugin packages are named `watchflow-<name>` on PyPI and live under `plugins/<name>/` in the first-party monorepo; third-party plugins live in their own repositories entirely.
