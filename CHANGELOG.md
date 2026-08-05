# Changelog

All notable changes to ScorpioWatch are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is below `1.0.0` the project is in its pre-release band: the CLI and the
embeddable API may change between minor versions, and no public stability guarantees apply
(see [`docs/ROADMAP.md`](./docs/ROADMAP.md)).

## [0.1.0] — 2026-08-02

First public release. A complete, working ingestion → match → execute pipeline: watch a
directory, match changes against glob triggers, and run a linear workflow of subprocess steps.

### Added

**The engine**

- **`FilesystemAdapter`** — asynchronous filesystem watching built on `watchfiles`, with
  debounced change batches. Linux is the supported target for this release; macOS and Windows
  run in CI but are advisory until their dedicated adapters land.
- **`EventBus`** — a bounded, backpressure-aware in-process publish/subscribe bus. Every
  subscriber gets its own bounded queue, so one slow consumer cannot stall another; when a
  queue fills, the configured `BackpressureStrategy` decides the outcome, and any drop is
  counted and logged as `queue.dropped` rather than passing silently.
- **`TriggerEngine`** — glob-pattern matching of events to triggers, with per-trigger
  evaluation.
- **`Scheduler`** — admits each fired trigger's workflow to the executor and owns the `Run`
  lifecycle, including a **leading-edge cooldown** keyed on `(trigger, matched_path)`. On by
  default, it runs the first event in a burst immediately and suppresses the rest, reporting
  what it held back as `admission.suppressed`. (Trailing-edge debounce is a separate, later
  feature; this release ships only the leading-edge throttle.)
- **`Executor`** — runs a linear workflow of subprocess steps with per-step opt-in
  `timeout_s`, process-group teardown so no orphans survive a cancellation, and streamed,
  bounded output capture that cannot exhaust memory on a chatty command.
- **`Engine`** — the single public embeddable entry point: `from swatch import Engine`.

**The CLI**

- **`swatch init [PATH]`** — scaffolds a starter `swatch.toml`. The generated config is
  language-neutral: one working trigger plus commented Python, JS/TS, Rust, Go, and
  full-stack examples to uncomment.
- **`swatch run [PATH]`** — watches `PATH` and runs each matching trigger's workflow.
- **`--once`** — process the first matching batch and exit, for CI and scripted use.
- **Four output modes from one code path** — the default is quiet on success (a one-line
  verdict per run, with a failing step's output shown in full); `--verbose` streams every
  subprocess line plus full engine records; `--quiet` prints only the final tally plus any
  failure's output; `--json` emits one JSON event per line for machine consumption.
- **Exit-code contract** — `0` success · `1` a run failed · `2` config error · `3` usage
  error · `4` startup error · `130` SIGINT · `143` SIGTERM.

**Configuration and safety**

- `swatch.toml`, validated with `pydantic` — unknown keys and malformed values are rejected
  at load time with a pointed error rather than failing later mid-run.
- **Strict subprocess execution.** Every step runs via `exec` with `shell=False`; there is no
  shell interpolation anywhere in the execution path, and `asyncio.create_subprocess_shell` is
  banned at lint time.
- **Scrubbed child environment.** A step's process starts from an empty environment; only the
  variables named in that step's `env_allowlist` are forwarded. Nothing leaks from the parent
  by default.
- **Language-agnostic by design.** A step is an `argv` list, so any executable is a
  first-class citizen — `pytest`, `npm test`, `cargo check`, `go build`, a shell script, or
  your own binary. Nothing about the engine is Python-specific.

### Notes

- **Install name and command name differ, deliberately.** The distribution is
  **`scorpiowatch`** on PyPI; it installs a **`swatch`** executable, reads `swatch.toml`, and
  imports as `swatch`:

  ```
  pip install scorpiowatch   →   swatch run .   →   from swatch import Engine
  ```

- Requires Python 3.12 or newer.

[0.1.0]: https://github.com/scorpiocodex/scorpiowatch/releases/tag/v0.1.0
