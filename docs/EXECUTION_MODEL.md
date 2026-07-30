# Execution Model

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index. Governs the runtime behavior of the modules in [`MODULE_SPECIFICATIONS.md`](./MODULE_SPECIFICATIONS.md).*

---

## 1. Run lifecycle

A `Run` is one execution instance of a `Workflow`. Its state machine:

```
                 ┌─────────┐
                 │ PENDING │  admitted by Scheduler, awaiting a free slot
                 └────┬────┘
                      ▼
                 ┌─────────┐
                 │ QUEUED  │  waiting on max_parallel / dependency ordering
                 └────┬────┘
                      ▼
                 ┌─────────┐
                 │ RUNNING │  DAGExecutor actively executing Steps
                 └────┬────┘
          ┌───────────┼───────────┬───────────────┐
          ▼           ▼           ▼               ▼
     ┌─────────┐ ┌────────┐ ┌──────────┐   ┌────────────┐
     │SUCCEEDED│ │ FAILED │ │ SKIPPED  │   │ TIMED_OUT  │
     └─────────┘ └────────┘ └──────────┘   └────────────┘
                                  ▲
                                  │ (downstream of a failed node
                                  │  without continue_on_fail)
          any of the above may instead resolve to:
                 ┌───────────┐
                 │ CANCELLED │  (external cancellation, e.g. daemon shutdown)
                 └───────────┘
```

Every transition is recorded in the `EventStore` with a timestamp, so a `Run`'s full history is reconstructable after the fact — including for `Run`s that never got past `PENDING` because the Scheduler rejected them (dedupe hit, cooldown active, rate limit).

---

## 2. Step execution model

Each `Step` `kind` has its own safe-execution contract:

| Kind | Executes via | Safety guarantee |
|---|---|---|
| `subprocess` | `asyncio.create_subprocess_exec` | `shell=False` always, argv as `list[str]`, cwd-jailed, env-scrubbed |
| `plugin` | Plugin's registered async callable | Runs under the plugin's granted capabilities only (see [`SECURITY_MODEL.md`](./SECURITY_MODEL.md)) |
| `http` | `httpx.AsyncClient` | Timeout-bound; response body size capped; no eval of response content |
| `mcp_tool` | `MCPClientGateway.call_tool` | Target server + tool schema-validated; response treated as untrusted input (see [`MCP_INTEGRATION.md`](./MCP_INTEGRATION.md)) |

### 2.1 Subprocess output: streamed and bounded

A `subprocess` Step's stdout and stderr are handled two ways at once — a **live stream** and a **bounded record**:

- **Streamed.** Both pipes are drained concurrently (independent reader tasks, so a silent stream never stalls a chatty one) and each decoded chunk is handed to an optional output sink *as the child produces it* — output is visible live rather than buffered into a lump at exit. The sink is awaited, so a slow consumer back-pressures the reader, which stops pulling from the pipe, so the OS pipe buffer throttles the child (the `EventBus` BLOCK discipline, §4). The sink is the human/JSON rendering path ([`UI_DESIGN.md`](./UI_DESIGN.md) §4.3).
- **Bounded record.** The `StepResult` still retains the output, but capped per stream (**256 KiB, tail-biased**, with a one-line truncation notice when exceeded). The tail is kept because a failing run's summary and tracebacks sit at the *end*; a runaway producer is capped rather than retained whole, so a multi-minute chatty run cannot balloon memory. A machine consumer that needs everything reads the live stream (`--json`), which is not capped.

---

## 3. DAG execution semantics

- **Topological sort** determines execution order; independent branches run concurrently under `asyncio.TaskGroup`, bounded by the Workflow's `max_parallel`.
- **Critical path identification** surfaces which chain of `Step`s determines total `Run` duration — exposed via `watchflow dag show` and the TUI's DAG view.
- **Failure propagation:** a failed node marks all of its downstream dependents `SKIPPED` unless the dependent (or the failed node itself) is marked `continue_on_fail: true`.
- **Cycle detection** runs at config-validation time (`watchflow check`), not at Run time — a cyclic Workflow never reaches the Scheduler.
- **Result caching** (from the DAG-optimization release band): deterministic nodes with unchanged inputs may skip re-execution; opt-in per node.

---

## 4. Concurrency model

- Structured concurrency via `asyncio.TaskGroup` for every group of concurrently-running `Step`s within a `Run`.
- Cancellation is cooperative and propagates cleanly: cancelling a `Run` cancels every in-flight `Step`'s subprocess (including its process group, not just the asyncio task) and marks the `Run` `CANCELLED`.
- Per-Workflow `max_parallel` bounds concurrent `Step`s; per-engine `max_parallel` (Scheduler-level) bounds concurrent `Run`s.

---

## 5. Retries and idempotency

- Retries are opt-in per `Step` (`retries: N`, `backoff: exponential | fixed`) and only offered for `Step` kinds that declare themselves idempotent-safe by default (`http` with `GET`, `mcp_tool` calls marked side-effect-free) — a `subprocess` step must explicitly declare `idempotent: true` before retries are permitted, since WatchFlow cannot infer that safely on its own.
- The Scheduler's dedupe key (see [`MODULE_SPECIFICATIONS.md`](./MODULE_SPECIFICATIONS.md) §4) prevents duplicate admission of the same logical `Run` triggered by a debounced burst of the same underlying event.

---

## 6. Timeouts and resource limits

- Every `Step` has a hard timeout (`timeout_s`); expiry cancels the subprocess and its process group, not just the awaiting coroutine.
- `cwd` is validated to be inside the project root at Step-construction time (jailing, see [`SECURITY_MODEL.md`](./SECURITY_MODEL.md)).
- Soft memory/CPU limits are applied where the OS provides a mechanism (`resource` module on POSIX, Job Objects on Windows) — best-effort, not a security boundary in themselves.

---

## 7. Execution contexts

The same `Engine` and `DAGExecutor` run identically across four contexts; only the surrounding CLI/daemon/MCP entry point differs.

**Local Dev** — ephemeral, foreground, `--dry-run` (detect Triggers without executing) and `--once` (process one batch and exit) available for tight iteration.

**CI/CD** — deterministic, non-interactive, `--profile ci`; process exit code reflects aggregate `Run` status so it composes with existing CI runners.

**DevOps Daemon** — long-running, systemd/launchd-managed, crash-only design: on restart, the `Engine` reconciles its in-flight state against the `EventStore` rather than assuming a clean shutdown occurred.

**MCP-triggered** — `Run`s originating from an AI agent's tool call carry `mcp_origin` provenance (see [`SECURITY_MODEL.md`](./SECURITY_MODEL.md)); destructive `Step`s require explicit `allow_mcp_trigger` and optionally `requires_confirmation`.

### 7.1 Signal handling and graceful shutdown

The crash-only design above is the recovery path when the process dies *without* warning. `SIGTERM` and `SIGINT` are the orderly path, and the daemon and CLI contexts treat them as a **drain-then-escalate** sequence rather than an immediate kill:

- **`SIGTERM` (daemon shutdown — systemd stop, `docker stop`, Kubernetes pod termination).** On the first `SIGTERM` the `Engine` stops admitting new `Run`s — the `Scheduler` quiesces and the Source Adapters `stop()` — while every in-flight `Run` is allowed to **drain** to a terminal state. Draining is bounded by a configurable **shutdown grace period** (`shutdown_grace_s`, default **30s**). If a `Run` is still in `RUNNING` when the grace period expires, it is **escalated to `CANCELLED`**: cancellation propagates through the DAG exactly as in §4, terminating each in-flight `Step`'s subprocess *and its process group* per §6 — not just the awaiting coroutine. A second `SIGTERM` during the grace period skips the remaining wait and escalates immediately.
- **`SIGINT` (foreground `Ctrl-C`).** In an attached foreground run (Local Dev / CI contexts), the first `SIGINT` begins the same graceful drain as `SIGTERM`; a **second `SIGINT`** forces immediate cancellation of all in-flight `Run`s. This gives the developer a fast "I mean it" without losing the clean-drain default.
- **Interaction with the state machine.** Runs terminated by either signal resolve to `CANCELLED` (§1), which is recorded in the `EventStore` with its timestamp like any other transition, so a signalled shutdown is fully reconstructable after the fact. Runs that finish draining within the grace period keep their natural terminal state (`SUCCEEDED` / `FAILED` / `TIMED_OUT`). A shutdown never silently drops a `Run` record.
- **`SIGKILL`** cannot be handled; recovery after it is the crash-only reconciliation path (§7, DevOps Daemon) — the `Engine` reconciles in-flight state against the `EventStore` on next start.

### 7.2 Process exit codes

The CLI exit code is the machine-readable summary of a run, so it composes with CI runners, systemd `Restart=` policies, and container orchestration health logic. The full set:

| Code | Meaning | Emitted when |
|---|---|---|
| `0` | Success | All `Run`s in the batch reached `SUCCEEDED` (or `SKIPPED`); a daemon shut down cleanly within its grace window |
| `1` | Workflow failure | At least one `Run` reached `FAILED` or `TIMED_OUT` — the aggregate signal the CI/CD context (§7) exits on so `--once` / `--profile ci` composes with an existing runner |
| `2` | Config error | `watchflow.toml` failed schema validation, or a `Workflow` failed cycle detection at `watchflow check` time (§3) and never reached the `Scheduler` |
| `3` | Usage error | Malformed CLI invocation — unknown command, missing required argument, bad flag (surfaced by the `typer` layer before the `Engine` starts) |
| `4` | Startup / runtime error | The `Engine` failed to start or continue for an operational reason unrelated to workflow logic — e.g. a health/IPC socket bind failure, or a Source Adapter that could not initialize |
| `130` | Interrupted | Terminated by `SIGINT` before completion (128 + 2), matching shell convention; the `SIGTERM` analogue is `143` (128 + 15) |

Codes `1` and `2` are the two a CI pipeline distinguishes most often: a `1` means "your workflow ran and something it did failed," a `2` means "WatchFlow could not even accept your configuration." They are kept separate for exactly that reason.

---

## 8. Speculative execution

From the DAG-optimization release band onward (see [`ROADMAP.md`](./ROADMAP.md)): a `Trigger` marked `speculative: true` may begin running its `Workflow` pre-emptively on a high-confidence partial match against the *previous* event batch, committing the result if the next batch confirms the match or cancelling cleanly if it doesn't. This is strictly an optimization — a `Trigger` without `speculative: true` behaves exactly as described above with no change.

---

## 9. Failure isolation and reporting

A `Run`'s failure is contained to that `Run`: it never crashes the `Engine`, never blocks unrelated `Trigger`s from firing, and is always visible — logged via `structlog`, counted in metrics, and queryable in the `EventStore` — never swallowed silently (Article VIII, [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md)).
