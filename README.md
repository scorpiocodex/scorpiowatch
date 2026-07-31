<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&height=210&color=0:0B0D12,55:1E2433,100:C89A30&text=ScorpioWatch&fontColor=E8B847&fontSize=64&fontAlign=50&fontAlignY=36&desc=React%20to%20anything.%20Orchestrate%20everything.&descAlign=50&descAlignY=56&descSize=15&animation=fadeIn" alt="ScorpioWatch" />

<img src="https://readme-typing-svg.demolab.com/?font=JetBrains+Mono&weight=600&size=21&pause=800&duration=2600&color=E8B847&center=true&vCenter=true&width=700&height=46&lines=reactive+automation;watches+your+files;runs+your+workflows;language-agnostic+by+design" alt="reactive automation · watches your files · runs your workflows · language-agnostic by design" />

<br />

[![PyPI](https://img.shields.io/pypi/v/scorpiowatch?style=flat-square&label=pypi&labelColor=0B0D12&color=C89A30)](https://pypi.org/project/scorpiowatch/)
[![Python](https://img.shields.io/pypi/pyversions/scorpiowatch?style=flat-square&label=python&labelColor=0B0D12&color=C89A30)](https://pypi.org/project/scorpiowatch/)
[![CI](https://img.shields.io/github/actions/workflow/status/scorpiocodex/scorpiowatch/ci.yml?branch=main&style=flat-square&label=ci&labelColor=0B0D12&color=5DCAA5)](https://github.com/scorpiocodex/scorpiowatch/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-C89A30?style=flat-square&labelColor=0B0D12)](./LICENSE)
[![Ruff](https://img.shields.io/badge/lint-ruff-C89A30?style=flat-square&labelColor=0B0D12)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/mypy-strict-C89A30?style=flat-square&labelColor=0B0D12)](https://mypy-lang.org/)

<br />

<img src="https://raw.githubusercontent.com/scorpiocodex/scorpiowatch/main/demo/demo.gif" alt="swatch watching a project: a file is saved, the tests rerun, the run reports a green verdict" width="860" />

<sub>A real recording — the toy project in <a href="./demo">demo/</a>, real <code>pytest</code>, real output. Not a mockup.</sub>

</div>

---

**ScorpioWatch** sits between *"something happened"* and *"the right work ran, safely, with a
record of it"*: it watches your files, matches changes against Triggers you declare, and runs
your commands — async top to bottom, never through a shell, one run per save.

It is **language-agnostic by design**. A Trigger matches paths by glob and runs any `command`
(an argv list) as a subprocess. The engine knows nothing about languages or toolchains: point
it at Python, Node, Rust, Go, or all of them in one monorepo, and supply the command.

## Install

```bash
pip install scorpiowatch
```

> [!IMPORTANT]
> **The distribution is `scorpiowatch`; the command and the import are `swatch`.** This split
> is deliberate — `pip install scorpiowatch` gives you a `swatch` executable, a `swatch.toml`
> config file, and `import swatch` in Python. If you are looking for a `scorpiowatch` command,
> there isn't one.

| | |
|---|---|
| Install with | `pip install scorpiowatch` |
| Run as | `swatch run .` |
| Configure in | `swatch.toml` |
| Import as | `import swatch` |

Requires Python 3.12+.

## Quickstart

```bash
swatch init .        # scaffold a starter swatch.toml
$EDITOR swatch.toml  # point it at your project's real command
swatch run .         # watch, and run on every change
```

`swatch init` writes a language-neutral starter: one working trigger plus commented,
ready-to-uncomment examples for Python, JS/TS, Rust, Go, and a two-language full-stack setup.
Replace the command with yours:

```toml
[[trigger]]
name = "tests"
source = "filesystem"
patterns = ["**/*.py"]

  [trigger.workflow]
  steps = [
    { command = ["pytest", "-q"], timeout_s = 60, env_allowlist = ["PATH"] },
  ]
```

Then `swatch run .` and save a file:

```
  swatch  ·  engine starting

  ✓ config loaded    swatch.toml  ·  1 triggers, 1 workflows
  watching .  ·  1 triggers armed  ·  ^C to stop

  02:04:43  ·  tests  → started  r_5c1f
  02:04:44  ·  tests  ✓ succeeded  0.9s
  02:04:47  ·  tests  → started  r_8b69
  02:04:48  ·  tests  ✓ succeeded  0.9s
```

Your program's own output is deliberately absent there. The default view is
**quiet-on-success, loud-on-failure**: while a step runs you get a single transient liveness
line, and only when a step fails is its output printed — framed, so the program's voice stays
visually distinct from the engine's. `--verbose` streams everything; `--json` emits one event
per line for machines.

> [!NOTE]
> `env_allowlist` is worth understanding early. A step's child process starts from a **fully
> scrubbed environment** — it inherits nothing, which is a security property. But the
> environment is also how the OS finds the program you named, so without `PATH` a bare
> `pytest` is not found at all. Allowlist what a step genuinely needs; nothing else leaks
> through.

## One config, several languages

A polyglot monorepo is not a special case — it is two triggers, each with its own `cwd`:

```toml
[[trigger]]
name = "frontend"
source = "filesystem"
patterns = ["frontend/**/*.ts", "frontend/**/*.tsx"]

  [trigger.workflow]
  steps = [
    { name = "test", command = ["npm", "test"], cwd = "frontend", timeout_s = 300 },
  ]

[[trigger]]
name = "backend"
source = "filesystem"
patterns = ["backend/**/*.py"]

  [trigger.workflow]
  steps = [
    { name = "test", command = ["pytest", "-q"], cwd = "backend", timeout_s = 300 },
  ]
```

`cwd` defaults to the watched root when unset. More in [`examples/`](./examples) — a
[local-dev](./examples/local-dev) config, this [full-stack](./examples/fullstack) one, and the
[demo project](./demo/project) the animation above records against.

## What v0.1.0 actually does

Everything listed here ships today and is exercised by the test suite.

- **Async-native throughout.** Every stage is `async`; the `EventBus` is bounded with explicit
  backpressure. No polling, no `to_thread`, no silent drops.
- **Safe execution, by construction.** Every step runs through
  `asyncio.create_subprocess_exec` with `shell=False` — argv lists, never a shell string. No
  `shell=True` exists anywhere in the codebase, so command injection is not a bug class here.
- **Scrubbed environments.** A step's child inherits only what its `env_allowlist` names.
- **Opt-in timeouts with real teardown.** `timeout_s` per step; on expiry the whole process
  group is terminated, not just the direct child.
- **Bounded output capture.** stdout and stderr are drained concurrently and streamed as they
  are produced, with a capped tail retained — a runaway process cannot exhaust memory.
- **Leading-edge cooldown, on by default.** The burst of filesystem events one editor save
  produces collapses into a single Run, per `(trigger, path)`. Tunable per trigger.
- **Glob triggers.** `**` spans directories, `*` stays within a path segment; patterns are
  normalized to forward slashes so a config written on one OS matches on another.
- **Four output modes from one code path.** Default, `--verbose`, `--quiet`, and `--json` are
  renderings of the same events — they cannot drift apart.
- **CI-friendly.** `--once` processes the first matching batch and exits with a meaningful
  code.
- **Typed and covered.** `mypy --strict` clean, 100% test coverage, an import-linter contract
  enforcing the layered architecture.

**Not yet — these are roadmap, not features.** Cron/webhook/queue/git triggers, the MCP
gateway, the TUI, the daemon, the durable `EventStore`, parallel DAG workflows, plugins,
metrics, and OpenTelemetry are all designed and documented, but **not implemented in 0.1.0**.
See the [roadmap](./docs/ROADMAP.md) for what lands when.

## Command surface

v0.1.0 ships two commands. (`check`, `doctor`, `list`, `history`, `mcp`, and `tui` are
specified in [`UI_DESIGN.md`](./docs/UI_DESIGN.md) §4.2 but arrive in later versions.)

| Command | |
|---|---|
| `swatch init [PATH]` | Scaffold a starter `swatch.toml` into PATH (default `.`) |
| `swatch run [PATH]` | Watch PATH and run each Trigger's Workflow on a match |

| Flag | |
|---|---|
| `--config`, `-c` | Path to the config (default: `PATH/swatch.toml`) |
| `--once` | Process the first matching batch, then exit |
| `--verbose`, `-v` | Stream all subprocess output, plus full engine records |
| `--quiet`, `-q` | Only the final tally, plus any failure's output |
| `--json` | Machine output: one JSON event per line |
| `--force`, `-f` | *(`init`)* Overwrite an existing `swatch.toml` |

Exit codes: `0` success · `1` a Run failed · `2` config error · `3` usage error · `4` startup
failure · `130` interrupted.

## Documentation

The [`docs/`](./docs) set is the substance behind all of the above.

| | |
|---|---|
| [SCORPIOWATCH.md](./docs/SCORPIOWATCH.md) | Project overview and the index to everything else |
| [ARCHITECTURE.md](./docs/ARCHITECTURE.md) | Layered architecture, data flow, deployment topologies |
| [EXECUTION_MODEL.md](./docs/EXECUTION_MODEL.md) | Run lifecycle, concurrency, retries, exit codes |
| [SECURITY_MODEL.md](./docs/SECURITY_MODEL.md) | Threat model, trust boundaries, subprocess safety |
| [UI_DESIGN.md](./docs/UI_DESIGN.md) | The design system, CLI output conventions, TUI spec |
| [ROADMAP.md](./docs/ROADMAP.md) | Version roadmap, release strategy, LTS bands |
| [DECISION_LOG.md](./docs/DECISION_LOG.md) | Architectural Decision Records — the *why* |
| [CODING_STANDARD.md](./docs/CODING_STANDARD.md) | Style, typing, testing, forbidden patterns |

## Status

**v0.1.0 — early release.** The engine described above is real, tested, and works; the API is
not frozen and there are no stability guarantees yet. Filesystem watching is exercised on
Linux, macOS, and Windows, with Linux the CI-gated target for this version and the other two
advisory ([`ROADMAP.md`](./docs/ROADMAP.md)).

Issues and discussion: [github.com/scorpiocodex/scorpiowatch](https://github.com/scorpiocodex/scorpiowatch/issues).

## License

[MIT](./LICENSE) © San Shibu (`ScorpioCodeX`)
