# Coding Standard

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index. Implements the values in [`ENGINEERING_PRINCIPLES.md`](./ENGINEERING_PRINCIPLES.md).*

---

## 1. Technical stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Native `asyncio.TaskGroup`, structured concurrency, faster startup |
| CLI framework | `typer` + `rich` | Typed commands, readable output, plays well with `--json` |
| Filesystem adapter | `watchfiles` (Rust-backed) | Async-native, no thread bridge |
| Async SQLite | `aiosqlite` | Non-blocking writes, WAL mode |
| HTTP client | `httpx` | Async-native, HTTP/2; webhook/queue/CI adapters and MCP HTTP transport |
| Logging | `structlog` | Structured, JSON-able, contextual |
| Validation | `pydantic` v2 | Config schema, plugin contracts, MCP tool schemas |
| Testing | `pytest` + `pytest-asyncio` + `hypothesis` | Async tests + property tests for scheduler/DAG |
| Type checking | `mypy --strict` | CI-blocking |
| Linting/formatting | `ruff check` / `ruff format` | Single fast tool |
| Packaging | `uv` + `hatchling` | Modern, reproducible builds |
| MCP transport | official `mcp` Python SDK | Standards-compliant server/client implementation |
| TUI (optional) | `textual` | Reactive, same philosophy as `rich` |

---

## 2. Repository-wide conventions

- **Naming:** modules and packages `snake_case`; classes `PascalCase`; functions/variables `snake_case`; `Protocol`/ABC interfaces suffixed `...Adapter`, `...Step`, `...Exporter` for extension points.
- **Typing:** `mypy --strict` passes on every module. `Any` is permitted only at the literal boundary where an external, untyped payload enters the system (e.g., a raw webhook body before validation) — and must be narrowed to a `pydantic` model within the same function.
- **Docstrings:** Google-style (`Args:` / `Returns:` / `Raises:`) on every public function, class, and plugin hook.
- **Imports:** absolute imports only within `src/watchflow`; no wildcard imports; import order enforced by `ruff` (`stdlib` → `third-party` → local).
- **Line length:** 100 columns, enforced by `ruff format`.

---

## 3. Layering enforcement

The four architectural layers in [`ARCHITECTURE.md`](./ARCHITECTURE.md) are enforced mechanically, not just by convention — a single import-linter `layers` contract in CI fails the build whenever a lower layer imports a higher one. In particular `watchflow.core` imports nothing from `watchflow.adapters.*` (the `SourceAdapter` Protocol it needs lives in `watchflow.core.ports` — ADR-0010 Option A), nothing from `watchflow.config` (the direction is one-way, `config → core` — ADR-0012), and nothing from `watchflow.plugins.*`, `watchflow.cli`, or `watchflow.tui`.

---

## 4. Testing standards

- Framework: `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`).
- Property-based tests via `hypothesis` are required for: dedupe-key collision resistance, cooldown-window correctness, and DAG cycle detection.
- Coverage floor: **80%**, both line and branch — CI-blocking.
- Cross-platform CI matrix: Linux, macOS, Windows — required for any change touching an adapter; core-only changes may run on Linux alone since the core is platform-blind by design.
- Every plugin hook and every MCP tool binding ships with at least one test using the official test harness (see [`PLUGIN_SPECIFICATION.md`](./PLUGIN_SPECIFICATION.md)).

---

## 5. Commit conventions

Conventional Commits: `<type>(<scope>): <subject>`.

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`, `build`, `revert`.
Scope: the module name (e.g., `scheduler`, `mcp`, `dag`, `cli`).

Enforced via `commitizen` (`cz check`) in CI. Example: `feat(mcp): add get_run_status tool to server gateway`.

---

## 6. Forbidden patterns

CI-blocking, no exceptions:

- `shell=True` anywhere, in any form (`subprocess.run`, `subprocess.Popen`, `asyncio.create_subprocess_shell`) — enforced by `ruff`'s built-in flake8-bandit rules `S602`/`S604`/`S605`, plus a flake8-tidy-imports banned-api entry for `asyncio.create_subprocess_shell` (which spawns via a shell but carries no `shell=True` keyword for bandit to catch). `ruff` exposes no user-authored-rule API — this is built-in rules plus a banned-api entry, not a custom rule.
- Bare `except:` (must catch a specific exception type, or `except Exception` with an explicit re-raise/log).
- `print()` for anything other than CLI-layer user-facing output — core and adapter code uses `structlog` exclusively.
- Mutable default arguments (`def f(x=[])`).
- Module-level mutable global state used for cross-request/cross-run coordination.
- `eval()` / `exec()` outside an explicitly opt-in, sandboxed plugin execution context.
- Blocking calls (`time.sleep`, synchronous `requests`, unwrapped synchronous file I/O) inside any `async def` in core or adapter code.
- Bounded queues instantiated without an explicit `maxsize`.

---

## 7. PR / CI gate checklist

A pull request merges only when all of the following are green:

- [ ] `mypy --strict`
- [ ] `ruff check` and `ruff format --check`
- [ ] `pytest` (unit + property + integration) at ≥80% line and branch coverage
- [ ] Cross-platform matrix (if adapter code touched)
- [ ] `pip-audit` / Dependabot check clean
- [ ] Conventional-commit-formatted commit messages
- [ ] Public API changes documented with updated docstrings and, if breaking, an RFC reference

---

## 8. Annotated example

```python
async def dispatch_step(
    step: Step,
    *,
    timeout_s: float,
    env_allowlist: Sequence[str],
) -> StepResult:
    """Execute a single Step and return its result.

    Args:
        step: The Step to execute, already validated against its
            pydantic schema by the caller.
        timeout_s: Hard timeout; the subprocess is cancelled if exceeded.
        env_allowlist: Environment variable names permitted to pass
            through to the child process. Everything else is scrubbed.

    Returns:
        A StepResult carrying exit status, captured output, and duration.

    Raises:
        StepTimeoutError: If the step does not complete within timeout_s.
    """
    env = {k: os.environ[k] for k in env_allowlist if k in os.environ}
    async with asyncio.timeout(timeout_s):
        proc = await asyncio.create_subprocess_exec(
            *step.command,               # list[str], never a joined string
            cwd=step.cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    return StepResult(
        exit_code=proc.returncode,
        stdout=stdout.decode(),
        stderr=stderr.decode(),
    )
```

This example demonstrates: strict typing, Google-style docstring, `shell=False` implicit via `create_subprocess_exec`, `argv` as a list, explicit timeout via structured `asyncio.timeout`, and an explicit environment allowlist rather than passthrough of the full environment.
