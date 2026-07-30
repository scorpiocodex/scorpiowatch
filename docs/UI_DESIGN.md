# UI Design

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index. Governed by [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md) — in particular §4 ("not a GUI-first product") and Article X ("the UI is always a consumer, never a dependency").*

WatchFlow has exactly two interface surfaces: a **CLI** for one-shot, scripted, and CI use, and an optional **TUI** for live observability. Both are terminal-native, both are read-only projections of the same observability bus (`structlog` events, `EventStore` queries), and neither is required to run the engine. This document is the single source of truth for how both look and behave, so a contributor building a new command or a new panel has a spec to match instead of guesswork to reverse-engineer from existing code.

---

## 1. Design principles

1. **One visual language, two surfaces.** The CLI and TUI share the same color semantics, the same terminology, and the same iconography. Learning one means already knowing the other.
2. **Color is signal, not decoration.** Every color in §2.1 carries one fixed meaning, reused identically everywhere it appears. Nothing is colored because it "looks nice" in one spot.
3. **Never color-dependent.** Every state that has a color also has a symbol and a word. `✓`/`✗`/`⚠` accompany green/red/orange everywhere, so a monochrome terminal or a colorblind reader loses zero information (§5).
4. **Terminal-native density.** This is a tool for people staring at it mid edit-test-loop, not a marketing surface — density over whitespace. But density never comes at the cost of a missing unit, timestamp, or label.
5. **Headless-safe.** Nothing the engine does depends on a terminal being attached. TUI panels, human-readable CLI output, `--json`, and the MCP Gateway's `query_event_history` are four projections of one identical event stream — never four separate code paths that can quietly disagree (ADR-0008, [`DECISION_LOG.md`](./DECISION_LOG.md)).

---

## 2. Shared design system

Both surfaces draw from the same token set below. A `rich.Theme` (CLI) and the mockup's CSS custom properties (TUI) are two renderings of these exact values — neither hand-picks its own palette.

### 2.1 Color palette

| Swatch | Hex | Semantic meaning | Used for |
|---|---|---|---|
| Amber (bright) | `#E8B847` | Primary / brand / active | Active tab text, KPI tile values, highlighted state |
| Amber (deep) | `#C89A30` | Brand accent | Header underlines, progress-bar fill, status-bar background |
| Amber (tint bg) | `#1a1610` | Active-state surface | Background behind a "running" DAG node or a banner |
| Green | `#5DCAA5` | Success / nominal / done | `✓`, `succeeded`, positive trends, "done" DAG nodes |
| Green (deep) | `#0F6E56` | Success border | Border on "done" DAG nodes |
| Green (tint bg) | `#0d1614` | Done-state surface | Background behind a completed DAG node |
| Red | `#E24B4A` | Error / failure / destructive | `✗`, `failed`, deleted-file events |
| Orange | `#EF9F27` | Warning / in-progress | `⚠`, modified-file events, "running" progress bars |
| Blue | `#85B7EB` | Values / variables | Variable names in code samples, informational IDs |
| Primary text | `#C8CCD4` | Default foreground | Body text |
| Secondary text | `#8A8F99` | De-emphasized labels | Field labels, panel headers, row keys |
| Tertiary text | `#5A5F69` | Dimmest / metadata | Timestamps, byte counts, comments |
| Background | `#0B0D12` | Base surface | Terminal / app background, feeds, code blocks |
| Panel background | `#0F1219` / `#131722` | Raised surface | Titlebar, tabs, panels, tiles, DAG node fill |
| Border | `#1E2433` | Structure | Every hairline border, divider, and unhighlighted DAG edge |

The three title-bar dots (red / orange / green) are a decorative macOS-style window affordance — they carry **no** status meaning, unlike every other use of these same three colors.

### 2.2 Typography

- **Font:** monospace stack — `var(--font-mono)`, falling back to `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`.
- **Base size:** 12px / 1.5 line-height for body content.
- **Scale:** KPI values 22px, weight 500; section headers 9.5–11px, uppercase, `0.1–0.12em` letter-spacing; timestamps and dim metadata 9–10.5px.
- **Numerals:** `font-variant-numeric: tabular-nums` wherever a number updates live (KPI tiles, tables, confidence scores) — digits must not jitter horizontally as they change.

### 2.3 Iconography

| Symbol | Meaning | Where |
|---|---|---|
| `◈` | Status view | Tab icon |
| `⊕` | Stream view | Tab icon |
| `◎` | Trigger view | Tab icon |
| `▷` | Execute view | Tab icon |
| `⊞` | DAG view | Tab icon |
| `◫` | Storage view | Tab icon |
| `◉` | Observe view | Tab icon |
| `✓` / `✗` / `⚠` | Success / failure / warning | CLI output, TUI status rows — always paired with a word |
| `▲` / `▼` | Positive / negative trend | KPI tile trend lines |
| `●` (colored) | Discrete state: done / running / queued / failed | DAG node-state legend |
| pulsing dot | Live / streaming | Any panel receiving live updates |

### 2.4 Layout grid

- **KPI tiles:** 4-column grid, equal width. Never more than 4 in a row — a 5th metric means a new panel, not a 5th column.
- **Detail panels:** full-width, or a flexible-left / fixed-210px-right two-column split (main content + compact sidebar).
- **Spacing:** 9–10px internal panel padding; 8–12px gaps between sibling panels.
- **Chrome:** 1px `#1E2433` borders, 3–4px corner radius — sharp enough to read as "terminal," soft enough not to look like a raw `tty`.

---

## 3. TUI design

The interactive reference implementation of this section is [`watchflow_terminal_mockup_7_views.html`](./watchflow_terminal_mockup_7_views.html) — treat it as the living spec; this section is the annotated version. The TUI is a read-only observability window (Article X): it renders the same events the CLI and the MCP Gateway's `query_event_history` tool serve, and holds no privileged access to engine internals.

### 3.1 Structure

```
┌─────────────────────────────────────────────────────────┐
│ ● ● ●   watchflow · event-driven workflow orchestration  │  titlebar (traffic lights + build meta)
├─────────────────────────────────────────────────────────┤
│ ◈Status ⊕Stream ◎Trigger ▷Execute ⊞DAG ◫Storage ◉Observe │  tabs
├─────────────────────────────────────────────────────────┤
│                                                           │
│                      (active view)                       │  body · min-height 430px
│                                                           │
├─────────────────────────────────────────────────────────┤
│ [q]quit [⇥]next [1-7]jump [/]search [r]reload [p]pause   │  status bar (amber, inverted)
│ [f]filter [?]help                              ● RUNNING │
└─────────────────────────────────────────────────────────┘
```

### 3.2 The seven views

| # | View | Shows | Key components |
|---|---|---|---|
| 1 | **Status** | At-a-glance engine health | 4 KPI tiles (events/sec, triggers fired, queue depth, exec latency); active-Workflow progress bars; engine-health panel (incl. MCP Gateway status); live recent-events feed |
| 2 | **Stream** | Raw event ingestion across every Source Adapter | Live scrolling feed; debounce-batcher panel; backpressure panel; an annotated code sample of the adapter's `listen()` loop |
| 3 | **Trigger** | `TriggerEngine` matching in action | Confidence-score bars per candidate match; active pattern/rule registry; LRU cache stats |
| 4 | **Execute** | A single `Step`'s execution (`Executor.run_step`) | Stage-flow strip (trigger → match → schedule → execute → observe); the exact subprocess invocation (`shell=False`, argv as `list[str]`); live progress bar; safety-invariant panel |
| 5 | **DAG** | A `Workflow`'s full graph (`DAGExecutor`) | SVG node/edge graph with critical-path highlighting; graph stats (nodes/edges/parallelism); node-state legend (done/running/queued/failed) |
| 6 | **Storage** | `EventStore` activity | Write-throughput KPIs; recent-batch-write table; schema reference |
| 7 | **Observe** | Structured logs + exporter status | Live `structlog` feed (info/warn/error); async-task breakdown; exporter status panel (structlog, Prometheus, OTLP, journald, **MCP Gateway**, TUI) |

### 3.3 Navigation and keybindings

| Key | Action |
|---|---|
| `q` | Quit the TUI (the engine keeps running headless) |
| `⇥` (Tab) | Next view |
| `1`–`7` | Jump directly to a view |
| `/` | Search within the active view's feed or table |
| `r` | Reload / refresh |
| `p` | Pause live updates (freeze the current frame for inspection) |
| `f` | Filter — by Trigger, Step kind, or log level, depending on the active view |
| `?` | Help overlay |

### 3.4 Motion and state conventions

- **Live pulse:** a small dot with a 1.5–1.8s opacity pulse marks any panel receiving live updates, paired with a `live` / `streaming` text badge — never the dot alone.
- **Blinking cursor block (`▮`):** marks the currently-active stage in the Execute view's stage-flow strip.
- **Bar fills** animate over 0.6s on value change — long enough to read as motion, short enough to never visibly lag the real update.
- **Accessibility:** every icon-only element (tab icons, state dots) carries a text label alongside it. The mockup's hidden `sr-only` heading — a screen-reader-visible summary enumerating all seven views — is a required pattern for any view added in the future, not a one-off.

---

## 4. CLI design

### 4.1 Philosophy

The CLI is built on `typer` + `rich` (see [`CODING_STANDARD.md`](./CODING_STANDARD.md) §1) using the *identical* color semantics as §2.1 — a single `rich.Theme` encodes the palette once, and every command imports it from that one place rather than hand-picking a slightly-different green.

Every command supports `--json`: the human-readable rendering below is always a projection of the same structured event, never a separate code path carrying different information.

### 4.2 Command reference

```
watchflow run [PATH]                start the engine
  --config FILE                     path to watchflow.toml
  --profile NAME                    named profile (dev | ci | prod)
  --json                            structured JSON output, one event per line
  --tui / --no-tui                  attach the TUI (default: headless)
  --dry-run                         evaluate Triggers, never execute
  --once                            process one event batch, then exit
  --verbose / -v                    full engine records + stream all subprocess output
  --quiet / -q                      only the final tally (plus any failure's output)

watchflow init                       scaffold a watchflow.toml
watchflow check                      validate config without running
watchflow doctor                     environment + health diagnostics
watchflow list triggers              show registered Triggers
watchflow list adapters              show available/active Source Adapters
watchflow list plugins               show loaded plugins and granted capabilities
watchflow history                    query the EventStore
  --since DURATION                   e.g. 1h, 24h, 7d
  --trigger NAME                     filter by Trigger
  --mcp-only                         show only MCP-originated Runs
  --limit N
watchflow dag show WORKFLOW          render a Workflow's DAG
watchflow plugin install NAME
watchflow plugin list
watchflow plugin remove NAME
watchflow mcp serve                  start the MCP server gateway
watchflow mcp tools list             list exposed tools and their permission requirements
watchflow mcp client test SERVER     verify connectivity to a configured external MCP server
watchflow daemon                     run as a long-lived, systemd/launchd-managed daemon
watchflow tui                        attach the TUI to an already-running engine
watchflow version
watchflow update
```

### 4.3 Annotated example outputs

**`watchflow run .`**
```
$ watchflow run .

  watchflow v1.0.0 · engine starting

  ✓ config loaded        watchflow.toml · 4 triggers, 2 workflows
  ✓ adapters ready       filesystem, cron, manual, mcp-trigger
  ✓ event store          .watchflow/events.db · wal · 7d retention
  ✓ mcp gateway          serving · stdio · 3 tools exposed

  watching .  ·  4 triggers armed  ·  ^C to stop, --tui to attach

  14:32:07  MOD  src/api.py                  → run-tests        matched  0.94
  14:32:07   ·   run-tests                   → started          r_5117
  14:32:09   ·   run-tests                   ✓ succeeded        1.8s
  14:32:41  NEW  tests/test_auth.py          → run-tests        matched  0.91
  14:32:41   ·   run-tests                   → started          r_5118
  14:32:44   ·   run-tests                   ✗ failed           2.6s  exit 1
```
*Colors: `MOD` orange, `NEW` green, `DEL` red — matching the Stream view's event-kind colors exactly. `✓ succeeded` green, `✗ failed` red, run IDs blue, everything else default or secondary text.*

**Two voices, and the default output policy.** `run` separates the **engine voice** (the lifecycle lines above: a run matched, started, and finished with its state + duration) from the **subprocess voice** (the watched program's own stdout/stderr). The engine's *raw* record — full UUIDs, argv, `timeout_s`, float durations — is never the default human view; the compact lines above are, and the raw record is available under `--verbose` and `--json`. The default policy for the subprocess voice is **quiet-on-success, loud-on-failure**:

- **While a step runs:** a single transient **liveness line** — a spinner + the run's name + elapsed time + the program's most recent output line — updated in place. It appears only after the step has run ~1s (so a fast step doesn't flash one) and is erased when the step finishes. It is TTY-only: piped or in CI it is inert, so no control codes leak into a captured log. This proves a long (e.g. 4-minute) run is alive without scrolling hundreds of lines past.
- **On success:** the program's output is not shown — the liveness line already proved progress.
- **On failure or timeout:** the failing step's retained output **tail** (the §2.1 bounded 256 KiB) is printed, framed as a titled block so the program's voice is visually distinct from the engine's.

`--verbose` streams **all** subprocess output live (success included; stdout→stdout, stderr→stderr) and shows the full engine records — the liveness spinner steps aside, since full output and a transient line cannot share the terminal region. `--quiet` prints only the final tally plus any failure's tail — no per-run lines, no spinner, no banner.

**`--json` and subprocess output.** In addition to the lifecycle events below, `--json` emits one `step.output` event per chunk **as it is produced** (streamed incrementally, never buffered into one event) and — unlike the human view — **uncapped**, since truncating a machine stream is worse than truncating a rendered one:
```
{"event":"step.output","run_id":"r_5117","stream":"stdout","text":"collected 42 items\n"}
```
Pre-run failures under `--json` (a config or startup error, before any run exists) currently render as the human `✗` block on **stderr**, keeping the stdout JSON stream clean; emitting them as machine-readable JSON error events is a possible later addition.

**Direction — docker-compose-style passthrough (not a v0.1.x deliverable).** The passthrough modes (`--verbose` today; parallel-step and daemon output later) will adopt compose-style attribution: each source (trigger/step) gets a **stable color**, a **prefix**, and **column-aligned** names, so interleaved concurrent output stays readable (you can tell at a glance which line came from which step). This is deliberately reserved for the "show me everything, and several things run at once" end of the dial — it earns its complexity only once there is genuine concurrency (the `DAGExecutor`, v2.0; the daemon, v1.1). It does **not** change the default view, which stays quiet-on-success.

**`watchflow check`**
```
$ watchflow check

  validating watchflow.toml …

  ✓ schema valid          4 triggers · 2 workflows · 0 warnings
  ✓ triggers              run-tests, run-tsc, on-push-review, deploy-on-request
  ✓ workflow DAGs         no cycles detected
  ✓ mcp exposure          2 workflows exposed as tools · 0 unscoped destructive steps

  config is valid.
```

**`watchflow doctor`**
```
$ watchflow doctor

  ✓ python 3.12.4
  ✓ platform              linux · inotify available
  ✓ event store           writable · 38.2 MB · wal mode
  ✓ plugins               7 loaded · 0 capability conflicts
  ⚠ mcp gateway           stdio only — streamable_http disabled
  ✓ disk space            412 GB free

  1 warning, 0 errors.
```

**`watchflow dag show deploy-on-request`**
```
$ watchflow dag show deploy-on-request

  Workflow: deploy-on-request  ·  3 steps  ·  critical path 7.2s

  trigger ──┬─▶ mypy ─────────┐
            ├─▶ pytest ───────┼─▶ report ─▶ notify
            └─▶ bandit ───────┘

  critical path:  trigger → pytest → report → notify   (7.2s)
```

**`watchflow history --since 1h --limit 5`**
```
  RUN ID    TRIGGER           STATUS       DURATION   ORIGIN
  r_5121    run-tests         succeeded    1.8s       filesystem
  r_5120    on-push-review    succeeded    4.2s       git
  r_5119    deploy-staging    succeeded    38.1s      mcp
  r_5118    run-tests         failed       2.6s       filesystem
  r_5117    run-tests         succeeded    1.9s       filesystem
```

**`watchflow mcp tools list`**
```
  MCP Gateway · server mode · stdio

  TOOL                  PERMISSION           DESCRIPTION
  trigger_workflow      allow_mcp_trigger    Start a Run of a named Workflow
  get_run_status        —                    Poll a Run's current state
  query_event_history   —                    Read from the EventStore
```

**`--json` mode (one JSON object per line, never a buffered array — `watchflow run` is a stream, not a one-shot response):**
```
$ watchflow run . --once --json
{"event":"trigger.detected","trigger":"run-tests","confidence":0.94,"ts":"2026-07-10T14:32:07.412Z"}
{"event":"run.started","run_id":"r_5117","trigger":"run-tests","ts":"2026-07-10T14:32:07.430Z"}
{"event":"run.completed","run_id":"r_5117","status":"succeeded","duration_s":1.8}
```

**Error output:**
```
$ watchflow run .

  ✗ config error

    watchflow.toml:14
      13 │ [[trigger]]
      14 │ name = "run-tests
         │                  ^ unterminated string

  fix the config, then re-run `watchflow check` to verify.
```

### 4.4 Exit codes

The authoritative table is [`EXECUTION_MODEL.md`](./EXECUTION_MODEL.md) §7.2; this mirrors it.

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | A Run failed (aggregate failure, relevant to `--once` / CI use) |
| `2` | Configuration error |
| `3` | Usage error (malformed CLI invocation, surfaced by `typer` before the Engine starts) |
| `4` | Engine startup / runtime failure (adapter unavailable, socket bind in use) |
| `130` | Interrupted (`SIGINT` / Ctrl+C) — the `SIGTERM` analogue is `143` |

---

## 5. Accessibility and fallback

- `NO_COLOR` (the informal standard) and an explicit `--no-color` flag both strip all ANSI color. Every message stays fully legible in plain text, because symbols and words (`✓`/`✗`/`⚠`, `succeeded`/`failed`) never depend on color alone (§1, principle 3).
- The TUI's hidden `sr-only` summary heading (§3.4) is a required pattern for every view, present or future.
- `--json` is the accessibility path of last resort for any shape not yet well-served by a screen reader — available on every command, with strictly no information loss relative to the human-readable form.

---

## 6. Consistency contract

Anyone adding a new CLI command or TUI view reuses, never reinvents:

- The exact palette in §2.1 — no new colors without updating this document first.
- The exact terminology from [`MODULE_SPECIFICATIONS.md`](./MODULE_SPECIFICATIONS.md) — `Trigger`, `Workflow`, `Step`, `Run` — never a synonym.
- The `✓` / `✗` / `⚠` + word pattern for status, everywhere, every time.

If a new surface needs something this document doesn't cover, that's a signal this document needs updating before the surface ships — not a cue to improvise.

---

*The living reference implementation of §3 is [`watchflow_terminal_mockup_7_views.html`](./watchflow_terminal_mockup_7_views.html). The CLI examples in §4 are illustrative target output, not captured from a running build — they are what a `rich`-based implementation should match, not a record of what already exists.*
