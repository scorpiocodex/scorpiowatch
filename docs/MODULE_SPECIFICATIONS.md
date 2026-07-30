# Module Specifications

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index. Corresponds to the boxes in [`ARCHITECTURE.md`](./ARCHITECTURE.md) and the files in [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md).*

Each entry: purpose, public interface, invariants, and testing notes. Signatures are illustrative of the contract, not a final API freeze.

---

## 1. `SourceAdapter` (port) — `core/ports.py`

**Purpose:** the abstract contract every event source implements, so the core never knows which concrete source produced an `Event`. The Protocol is owned by the Core layer (`core/ports.py`, ADR-0010 Option A); the concrete adapters in `adapters/` import and implement it.

```python
class SourceAdapter(Protocol):
    name: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def events(self) -> AsyncIterator[Event]: ...
```

**Invariants:** `events()` never raises for a single malformed upstream item — it logs and skips. `stop()` is idempotent and safe to call before `start()`.

**Built-in (core-bundled) implementations:** `FilesystemAdapter`, `CronAdapter`, `ManualAdapter`, `MCPTriggerAdapter`.
**Official plugin implementations:** `WebhookAdapter`, `QueueAdapter`, `GitAdapter`, `CIProviderAdapter` — see [`PLUGIN_SPECIFICATION.md`](./PLUGIN_SPECIFICATION.md).

**Testing:** every adapter ships a fake/deterministic test double so downstream modules never need a real filesystem or network call in unit tests.

---

## 2. `Event` and `EventBus` — `core/events.py`

```python
class Event(BaseModel):
    model_config = ConfigDict(frozen=True)  # immutable — safely fanned out by reference

    id: UUID
    source: str
    type: str
    payload: dict[str, Any]
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

class EventBus:
    def __init__(self, maxsize: int, backpressure: BackpressureStrategy): ...
    async def publish(self, event: Event) -> None: ...
    def subscribe(self, topic: str | None = None) -> AsyncIterator[Event]: ...
```

**Invariants:** bounded (`maxsize` always set); a full queue triggers the configured `BackpressureStrategy` (`drop_oldest | block | report_and_drop`) — never a silent, unmetriced drop. Multiple subscribers per topic; each subscriber has its own queue so one slow subscriber cannot stall another.

**Testing:** property tests confirm no event is delivered to a subscriber that unsubscribed before publish, and that `report_and_drop` always emits a `queue.dropped` metric.

---

## 3. `Trigger` and `TriggerEngine` — `core/triggers.py`

```python
class Trigger(BaseModel):
    name: str
    source: str                       # which adapter's events this trigger considers
    match: MatchSpec                  # glob | cron_expr | predicate | mcp_tool_name
    threshold: float = 0.5
    cooldown_ms: int = 0
    workflow: Workflow

class TriggerFired(BaseModel):
    trigger: Trigger                  # the trigger that matched
    event: Event                      # the event it matched against

class TriggerEngine:
    def register(self, trigger: Trigger) -> None: ...
    async def evaluate(self, event: Event) -> TriggerFired | None: ...
```

**Invariants:** confidence score is `pattern_match × recency × history_weight`, deterministic given the same inputs (required for the property tests in [`CODING_STANDARD.md`](./CODING_STANDARD.md)). A `Trigger` below its `threshold` produces no `TriggerFired` and no side effect.

**Testing:** hypothesis-generated event sequences confirm score is always in `[0, 1]` and monotonic in `history_weight`.

---

## 4. `Scheduler` — `core/scheduler.py`

```python
class Scheduler:
    def __init__(self, max_parallel: int, default_cooldown_ms: int): ...
    async def admit(self, fired: TriggerFired) -> Run | None: ...
```

**Purpose:** owns rate limiting, dedupe, cooldown, and (from the DAG-executor release band onward) speculative execution. Replaces the legacy prototype's tangled reactive loop entirely.

**Invariants:** dedupe key defaults to `sha256(trigger.name + sorted(event.payload keys touched))`; a `Run` is only admitted if outside its trigger's cooldown window and under `max_parallel`. MCP-originated `TriggerFired` events are rate-limited on a separate counter from human/cron-originated ones (see [`SECURITY_MODEL.md`](./SECURITY_MODEL.md)).

**Testing:** property tests for dedupe-key collision resistance and cooldown-window correctness are CI-required (see [`CODING_STANDARD.md`](./CODING_STANDARD.md)).

---

## 5. `Executor` and `DAGExecutor` — `execution/executor.py`, `execution/dag.py`

```python
class Executor:
    async def run_step(self, step: Step, ctx: RunContext) -> StepResult: ...

class DAGExecutor:
    async def run(self, workflow: Workflow, ctx: RunContext) -> RunResult: ...
```

**Purpose:** `Executor` runs one `Step`; `DAGExecutor` runs a `Workflow`'s full graph — topological sort, parallel fan-out of independent branches, critical-path identification, and `continue_on_fail` propagation.

**Invariants:** every `subprocess` step goes through `asyncio.create_subprocess_exec`, `shell=False`, argv as `list[str]` — no exception (Article III). A failed node without `continue_on_fail: true` marks all downstream nodes `skipped`, never silently omitted from the `RunResult`.

**Testing:** cycle detection is a required hypothesis property test; DAG fan-out concurrency is tested with injected artificial delays to confirm true parallelism, not just interleaving.

Full lifecycle semantics: [`EXECUTION_MODEL.md`](./EXECUTION_MODEL.md).

---

## 6. `EventStore` — `storage/event_store.py`

```python
class EventStore:
    async def append(self, event: Event) -> None: ...
    async def record_run(self, run: RunResult) -> None: ...
    async def query(self, filters: QueryFilters) -> list[EventRecord]: ...
```

**Purpose:** durable, queryable history of Events, `TriggerFired` records, and `Run`s.

**Invariants:** `aiosqlite`-backed, WAL mode, `synchronous=NORMAL`. Writes are batched (default: 50 events or 100ms, whichever first) — batching never delays a caller past the configured window even under load. Indexed on `ts DESC` for the common "recent history" query.

**Testing:** integration tests confirm no event is lost across an unclean process termination mid-batch (recovery via WAL replay).

---

## 7. `MCPGateway` — `mcp/gateway.py`, `mcp/server.py`, `mcp/client.py`

```python
class MCPServerGateway:
    def expose_tool(self, tool: MCPToolBinding) -> None: ...
    async def serve(self) -> None: ...

class MCPClientGateway:
    async def call_tool(self, server: str, tool: str, args: dict) -> MCPToolResult: ...
```

**Purpose:** the dual-mode integration layer described in [`MCP_INTEGRATION.md`](./MCP_INTEGRATION.md) — server mode exposes WatchFlow actions to AI agents; client mode lets a `mcp_tool` Step call an external MCP tool.

**Invariants:** every inbound and outbound call is schema-validated before touching the Scheduler or DAGExecutor; every resulting `Run` carries `mcp_origin` provenance metadata into the `EventStore` (see [`SECURITY_MODEL.md`](./SECURITY_MODEL.md)).

**Testing:** contract tests against the official MCP Python SDK's test transport; no test depends on a live external MCP server.

---

## 8. `PluginHost` — `plugins/host.py`

```python
class PluginHost:
    def discover(self) -> list[PluginManifest]: ...
    async def load(self, manifest: PluginManifest, grants: set[Capability]) -> Plugin: ...
```

**Purpose:** discovers plugins via `watchflow.plugins` entry points, checks declared capabilities against user grants, and manages plugin lifecycle hooks.

**Invariants:** a plugin requesting an ungranted capability fails to load with an explicit, actionable error — never a silent partial load. Full contract: [`PLUGIN_SPECIFICATION.md`](./PLUGIN_SPECIFICATION.md).

---

## 9. `Observability` — `observability/`

Three independent exporters, all subscribing to the same internal observability bus: `structlog` (always on), Prometheus (metrics), OpenTelemetry (tracing). The TUI is a fourth consumer of the identical bus — never a privileged one.

**Invariants:** no core or adapter module calls a logging/metrics function directly with unstructured strings; every log line is a structured event with a stable schema.

---

## 10. `ConfigLoader` — `config/loader.py`

```python
def load(path: Path) -> WatchflowConfig: ...
```

**Purpose:** parses and validates `watchflow.toml` and returns the core-owned `WatchflowConfig` (defined in `core/config.py` — ADR-0012); `config/` is a pure loader that defines no models of its own. `WatchflowConfig` is the single source of truth for what a valid config looks like — the CLI's `watchflow check` command is a thin wrapper over this same loader.

**Invariants:** invalid config fails fast with a precise, field-level error message — never a partially-applied configuration.

**v0.1.0 mapping note — pattern expansion:** a `[[trigger]]` declares `patterns` as a *list* of globs, but a core `Trigger` (§3) holds a single `MatchSpec`. The loader therefore expands one trigger with N patterns into **N core `Trigger`s** — one `GlobMatch` each, sharing the trigger's name and Workflow. This keeps the core model single-match (no spec-absent "match any of N" variant is introduced). The consequence, until dedupe lands in v0.3.0 (§4), is that a single event matching M of a trigger's patterns fires the shared Workflow M times; overlapping patterns should be avoided until then. (Keys *inside* a `[[trigger]]` are validated strictly — an unknown key is an error — while unknown *top-level* sections such as a future `[mcp]` are ignored so a forward-looking config still loads.)

---

## 11. `Engine` — `core/engine.py`

```python
class Engine:
    def __init__(self, config: WatchflowConfig): ...
    async def run(self) -> None: ...
    async def shutdown(self) -> None: ...
```

**Purpose:** the single public embeddable entry point (`from watchflow import Engine`) wiring every module above into one running instance — used identically by the CLI, the daemon, and any host process embedding WatchFlow directly.
