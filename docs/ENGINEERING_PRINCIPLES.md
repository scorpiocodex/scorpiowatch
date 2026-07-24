# Engineering Principles

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index.*

[`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md) states *why* WatchFlow exists and what it will not trade away. This document states *how* those commitments show up in ordinary, daily engineering decisions — the reasoning a contributor should run through when the constitution doesn't spell out the specific situation in front of them.

Where the constitution is law, this document is case law: rationale, contrast, and heuristics.

---

## 1. Async-first, no compromises

**Principle:** no blocking call ever executes on the event loop, including "just this once."

**Why:** a single blocking call anywhere in the hot path reintroduces the exact failure mode the legacy prototype died of — one slow consumer stalls every other subscriber on the same loop.

**Contrast:**
```python
# Anti-pattern: blocking call smuggled onto the loop
data = requests.get(url)          # blocks the entire event loop

# Correct
async with httpx.AsyncClient() as client:
    data = await client.get(url)
```
If a needed library has no async surface, wrap it behind an adapter boundary explicitly (documented, reviewed, isolated) rather than reaching for `to_thread` as a permanent fixture.

---

## 2. Safe execution by default

**Principle:** every subprocess spawn is `asyncio.create_subprocess_exec(*argv, ...)` with `argv` as a `list[str]`. There is no code path — not a debug flag, not a "trusted" plugin — where a shell string gets interpolated with user- or event-derived data.

**Contrast:**
```python
# Anti-pattern
await asyncio.create_subprocess_shell(f"pytest {changed_file}")

# Correct
await asyncio.create_subprocess_exec("pytest", changed_file)
```
This is not a style preference — it is the difference between a workflow config and a remote code execution vector when the triggering payload is a webhook body or an AI agent's tool-call arguments.

---

## 3. Backpressure-aware, never silently drop

**Principle:** every queue has a `maxsize`, and every overflow strategy is a deliberate, configured, metriced choice — never an unbounded queue that "just doesn't overflow in practice."

**Why:** unbounded queues turn a slow consumer into a memory leak; silent drops turn a slow consumer into a debugging nightmare six weeks later when someone asks why a workflow didn't run.

**Heuristic:** if you can't name the backpressure strategy (`drop_oldest`, `block`, `report_and_drop`) for a queue you're adding, you haven't finished designing it.

---

## 4. Structured concurrency over ad-hoc tasks

**Principle:** use `asyncio.TaskGroup` (or an equivalent structured scope) so that a group of related tasks has one lifetime, one cancellation point, and one place exceptions surface.

**Contrast:**
```python
# Anti-pattern: orphaned tasks, exceptions swallowed
for step in steps:
    asyncio.create_task(run_step(step))

# Correct
async with asyncio.TaskGroup() as tg:
    for step in steps:
        tg.create_task(run_step(step))
```

---

## 5. Explicit over implicit

**Principle:** configuration and inter-module contracts are typed (`pydantic` v2 models), not stringly-typed dicts passed around and hoped-for. If a `Step`'s `kind` field can be `"subprocess" | "plugin" | "http" | "mcp_tool"`, that's a `Literal`, not a free string checked with `if kind == "subprocess"` scattered through the codebase.

**Why:** implicit contracts are where cross-platform bugs and plugin-compatibility breaks hide until a user finds them.

---

## 6. Small, independently testable units

**Principle:** every core module should fit on one screen and be testable with a handful of unit tests that don't require the rest of the engine running.

**Heuristic carried over from the legacy prototype's failure mode:** if you can't explain what a module does in one sentence without the word "and," it's two modules.

---

## 7. Fail loud, recover gracefully

**Principle:** a failing Step fails its Run visibly — logged, metriced, recorded in the EventStore — never swallowed. But one Workflow's failure never takes down the engine; failure isolation is a hard boundary between Runs.

**Mechanism:** `continue_on_fail` is an explicit per-step opt-in for the rare case where downstream work should proceed anyway; the default is propagate-and-stop.

---

## 8. Idempotency by design

**Principle:** every Trigger has a dedupe key (default: `sha256(trigger_id + sorted_event_keys)`); retries of a Step are only offered for Step kinds that declare themselves safe to retry.

**Why:** in an event-driven system, "the same thing happened twice" is not an edge case — it's Tuesday. A workflow that isn't safe to run twice is a workflow waiting to cause an incident.

---

## 9. Documentation as code, not as afterthought

**Principle:** every public function, class, and plugin hook carries a docstring that specifies args, returns, and raises before the implementation is considered done — not before the next release.

---

## 10. Testing pyramid, concretely

| Layer | Tooling | What it covers |
|---|---|---|
| Unit | `pytest` + `pytest-asyncio` | Individual module behavior in isolation |
| Property | `hypothesis` | Scheduler dedupe/cooldown invariants, DAG cycle detection |
| Integration | `pytest` + real `aiosqlite` + fake adapters | Cross-module event flow |
| Cross-platform matrix | CI on Linux/macOS/Windows | Adapter-layer behavior only — core logic is platform-blind by design (Article II) |

Coverage floor: **80%**, line and branch, enforced in CI (see [`CODING_STANDARD.md`](./CODING_STANDARD.md)).

---

## 11. Decision heuristics — "when in doubt"

- If a change makes the core depend on a specific platform API: it belongs in an adapter, not the core.
- If a change makes the core depend on a specific third-party service (Slack, GitHub, a specific MQ broker): it belongs in a plugin.
- If a change would make a Workflow behave differently depending on whether it was triggered by a human or an AI agent *without the config saying so explicitly*: don't make the change.
- If you're about to add a boolean flag to bypass a safety check "for advanced users": stop, and re-read Article III.

---

## 12. Anti-goals

- **Premature abstraction.** Don't introduce a plugin interface for something that has exactly one implementation and no second one on the roadmap.
- **Config sprawl.** Every new `watchflow.toml` key should be justifiable in one sentence; if it needs a paragraph, it probably needs a plugin instead.
- **Silent magic.** Confidence scoring, dedupe, and cooldown are all inspectable and loggable at debug level — nothing in the matching or scheduling path should be a black box even to its own maintainers.
