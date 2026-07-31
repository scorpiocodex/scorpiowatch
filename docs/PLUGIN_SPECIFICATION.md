# Plugin Specification

*Part of the ScorpioWatch documentation set — see [`SCORPIOWATCH.md`](./SCORPIOWATCH.md) for the full index. Implements the extensibility surface described in [`ARCHITECTURE.md`](./ARCHITECTURE.md) §7.*

---

## 1. Plugin types

ScorpioWatch exposes exactly four extension points — no more, per Article V of [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md):

| Plugin type | Extends | Example |
|---|---|---|
| **Source Adapter plugin** | New event sources | Webhook listener, message queue, git hooks, CI provider webhooks |
| **Step plugin** | New Step `kind`s executable inside a Workflow | A typed Slack-post step, a GitHub check-run step |
| **Exporter plugin** | New observability sinks | OpenTelemetry, PagerDuty alerting |
| **MCP tool plugin** | New tools/resources on the MCP Gateway's server surface, or new client-side MCP bindings | Expose a custom internal action to AI agents |

---

## 2. Plugin manifest and discovery

Plugins are ordinary PyPI packages registering an entry point under `swatch.plugins`:

```toml
# pyproject.toml of a plugin package
[project.entry-points."swatch.plugins"]
scorpiowatch-slack = "swatch_slack:SlackPlugin"
```

At startup, `PluginHost.discover()` enumerates all registered entry points, reads each plugin's declared manifest (name, version, required capabilities, plugin type), and presents them for the user to grant capabilities to — nothing loads with implicit trust.

---

## 3. Plugin contract

```python
from swatch.plugins import Plugin, hookimpl

class SlackPlugin(Plugin):
    name = "scorpiowatch-slack"
    version = "0.1.0"
    plugin_type = "exporter"
    requires = {"network"}

    @hookimpl
    async def on_trigger_fired(self, fired: TriggerFired) -> None: ...

    @hookimpl
    async def on_run_start(self, run: Run) -> None: ...

    @hookimpl
    async def on_step_start(self, step: Step, run: Run) -> None: ...

    @hookimpl
    async def on_step_complete(self, result: StepResult, run: Run) -> None: ...

    @hookimpl
    async def on_run_complete(self, result: RunResult) -> None: ...

    @hookimpl
    async def on_mcp_tool_call(self, call: MCPToolCall) -> None: ...

    @hookimpl
    async def on_engine_start(self, engine: Engine) -> None: ...

    @hookimpl
    async def on_engine_stop(self, engine: Engine) -> None: ...
```

All hooks are optional to implement; a plugin implements only the ones relevant to its type. Hooks are `async` without exception, consistent with Article I.

---

## 4. Permission declaration

A plugin's `requires` set is checked against user-granted capabilities at load time (full capability list and rationale: [`SECURITY_MODEL.md`](./SECURITY_MODEL.md) §4). A plugin requesting `subprocess` or `mcp.server_expose` — the two highest-trust capabilities — triggers an explicit confirmation prompt on first load, not just a config-file grant.

---

## 5. Versioning and compatibility

- The plugin API itself is semantically versioned, independent of ScorpioWatch's own release train.
- A plugin declares `swatch_api = ">=1.2,<2.0"` in its manifest; `PluginHost` refuses to load a plugin outside its declared compatible range rather than risk an undefined hook signature mismatch.
- Breaking changes to the plugin API require a major bump of the plugin API version and are called out explicitly in [`ROADMAP.md`](./ROADMAP.md) and logged as an ADR in [`DECISION_LOG.md`](./DECISION_LOG.md).

---

## 6. Testing plugins

An official test harness (`swatch.testing`) provides:

- A fake `Engine` with in-memory `EventBus` and `EventStore`, so plugin tests never require a real filesystem watch or network call.
- Fixtures for synthesizing `Event`, `TriggerFired`, `Run`, and `StepResult` objects.
- A capability-grant simulator, so a plugin can assert it fails closed correctly when a capability is withheld.

Every official first-party plugin ships with at least one test per implemented hook (see [`CODING_STANDARD.md`](./CODING_STANDARD.md) §4).

---

## 7. Publishing

- Naming convention: `scorpiowatch-<name>` on PyPI.
- First-party official plugins live in the main repository under `plugins/<name>/`; third-party plugins are expected to live in their own repositories and are discovered purely via the entry-point mechanism — no central plugin registry is required for a plugin to work.
- A plugin marketplace metadata format and `swatch plugin install <name>` convenience command are planned for the plugin-platform release band (see [`ROADMAP.md`](./ROADMAP.md)); until then, plugins install via ordinary `pip install`.

---

## 8. Official plugin catalog

| Plugin | Type | Purpose |
|---|---|---|
| `scorpiowatch-webhook` | Source Adapter | Inbound HTTP webhook listener (opt-in network exposure) |
| `scorpiowatch-queue` | Source Adapter | Message queue consumer (Redis, NATS) |
| `scorpiowatch-git` | Source Adapter | Git hook / ref-change events |
| `scorpiowatch-ci` | Source Adapter | CI provider webhook parsing (GitHub Actions, GitLab CI payload schemas) |
| `scorpiowatch-slack` | Exporter | Post Run results to Slack |
| `scorpiowatch-github` | Exporter | Comment on PRs, create check runs |
| `scorpiowatch-notify` | Exporter | Desktop notifications (`libnotify`, `osascript`, Windows toast) |
| `scorpiowatch-otel` | Exporter | OpenTelemetry traces + metrics |
| `scorpiowatch-prom` | Exporter | Extended Prometheus metrics |
| `scorpiowatch-discord` | Exporter | Discord webhook |
| `scorpiowatch-pagerduty` | Exporter | Alert on Run failures |
| `scorpiowatch-mcp-agents` | MCP tool plugin | Curated tool bundles for common agent frameworks |
| `scorpiowatch-ai` | MCP tool plugin | LLM-assisted Trigger inference from observed event history |

**Decision to keep these as plugins rather than core:** see ADR-0006 in [`DECISION_LOG.md`](./DECISION_LOG.md) — filesystem, cron, manual, and MCP-trigger sources are core because they require no external service and no additional network trust boundary; everything else is a plugin by construction.
