# Security Model

*Part of the ScorpioWatch documentation set — see [`SCORPIOWATCH.md`](./SCORPIOWATCH.md) for the full index.*

---

## 1. Threat model

ScorpioWatch executes local commands in reaction to events that can, in several configurations, originate outside the machine it runs on. The security model exists to make sure "an event happened" never implies "arbitrary code may run."

**Adversaries the model defends against:**

| Adversary | Capability assumed |
|---|---|
| Malicious or buggy webhook sender | Controls the payload of any inbound webhook event |
| Malicious or vulnerable plugin | Runs inside the engine's process unless sandboxed |
| Malicious or compromised remote MCP server | Can send arbitrary tool-call requests/responses if configured as a client target |
| Malicious or over-permissioned AI agent caller | Can request any tool exposed by the MCP Gateway server mode, at any rate |
| Compromised upstream dependency | Present in the supply chain unless mitigated |
| Local, unprivileged user on a shared host | Cannot read another user's `EventStore`, config, or secrets |

**Explicitly out of scope:** protecting against a fully compromised host OS or a user with root/administrator access to the machine ScorpioWatch runs on.

---

## 2. Trust boundaries

```
 TRUSTED                SEMI-TRUSTED (sandboxed)         UNTRUSTED (validated at the door)
┌──────────────┐        ┌───────────────────┐            ┌───────────────────────────────┐
│ local config │        │ installed plugins │            │ webhook payloads               │
│ swatch.toml  │  ───▶  │ (capability-gated)│  ◀───────  │ queue messages                 │
└──────────────┘        └───────────────────┘            │ remote MCP server responses    │
                                                          │ AI agent tool-call arguments    │
                                                          └───────────────────────────────┘
```

Anything crossing from the untrusted zone into a Step's `argv` or environment must pass through explicit `pydantic` validation and the strict-subprocess execution model in §3 before it can influence what actually runs.

---

## 3. Subprocess execution safety

Restating Article III of [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md) at the mechanism level:

- `asyncio.create_subprocess_exec` only; `argv` is always a `list[str]` built from validated, typed fields — never a formatted or concatenated string.
- **cwd jailing:** a Step's working directory is resolved and checked to be inside the configured project root; symlink escapes are rejected.
- **Environment scrubbing:** only variables named in an explicit `env_passthrough` allowlist reach the child process. Everything else, including ambient secrets in the parent environment, is withheld by default.
- **Timeouts:** every Step has a hard timeout; on expiry, the subprocess and its process group are terminated, not just the asyncio task.
- **No implicit privilege escalation:** ScorpioWatch never re-execs itself with elevated privileges to satisfy a Step.

---

## 4. Plugin permission model

Plugins declare required capabilities up front; the user grants them per-plugin at install or first load:

| Capability | Grants |
|---|---|
| `filesystem.read` | Read access to files under the watched root |
| `filesystem.write` | Write access under the watched root |
| `network` | Outbound network calls (webhooks it sends, APIs it calls) |
| `subprocess` | Ability to register a new Step kind that spawns processes |
| `mcp.client` | Ability to call out to external MCP tools |
| `mcp.server_expose` | Ability to register additional tools/resources on the MCP Gateway's server surface |

A plugin that requests a capability it wasn't granted fails closed with a clear, actionable denial message — never a silent no-op. Full lifecycle and manifest format: [`PLUGIN_SPECIFICATION.md`](./PLUGIN_SPECIFICATION.md).

---

## 5. MCP-specific security

MCP integration introduces two new classes of caller that need explicit treatment — full detail in [`MCP_INTEGRATION.md`](./MCP_INTEGRATION.md); this section states the security invariants that document must satisfy.

- **Tool schema validation:** every MCP tool call's arguments are validated against a `pydantic` schema before touching the Scheduler or DAGExecutor — the same discipline as a webhook payload, not a shortcut.
- **Default-deny for destructive Steps:** a Workflow Step marked `destructive: true` (deletes, force-pushes, production deploys) cannot be triggered by an MCP tool call unless the Trigger explicitly sets `allow_mcp_trigger: true`, and may additionally require `requires_confirmation: true`, which blocks execution pending an explicit human acknowledgment.
- **Provenance tagging:** every `Run` originating from an MCP tool call carries `mcp_origin` metadata (caller identity if available, tool name, protocol version) into the `EventStore`, so the audit trail always distinguishes an AI-agent-initiated Run from a human- or cron-initiated one.
- **Separate rate limiting:** MCP-originated Runs are rate-limited independently of human/cron-originated Runs, so a misbehaving or looping agent cannot starve the Scheduler for everyone else.
- **Untrusted remote MCP servers:** when ScorpioWatch acts as an MCP *client* calling an external server (a Step of kind `mcp_tool`), that server's responses are treated as untrusted input — sanitized and schema-validated before any value from the response can influence a downstream Step's `argv`.

---

## 6. Secrets handling

- Secrets are never logged. `structlog` processors redact any field name matching a configurable secret-pattern list (`*_key`, `*_token`, `*_secret`, `password`) before a log line is emitted.
- Environment passthrough is allowlist-only (§3); there is no "pass everything" mode.
- ScorpioWatch does not store, generate, or rotate secrets itself (see Non-goals in [`PROJECT_CONSTITUTION.md`](./PROJECT_CONSTITUTION.md)) — it expects secrets to arrive via the environment or a dedicated secrets-manager plugin.

---

## 7. Supply chain

- Dependabot and `pip-audit` run in CI on every pull request.
- SPDX SBOM attached to every release from the first storage-API-bearing release onward.
- Publishing via PyPI Trusted Publisher — no long-lived API tokens in CI.
- Release artifacts signed via Sigstore from the DAG-executor release band onward (see [`ROADMAP.md`](./ROADMAP.md) for exact version targets).

---

## 8. Vulnerability disclosure

- **Reporting:** private disclosure via GitHub Security Advisories, documented in `SECURITY.md` at the repository root.
- **Acknowledgment SLA:** 48 hours.
- **Fix SLA:** 7 days for critical, 30 days for high, 90 days for medium/low.

---

## 9. Audit logging

Every one of the following is recorded, unconditionally, in the `EventStore` — not just in a rotating log file:

- Every subprocess spawn (command, cwd, timeout, exit code, duration).
- Every plugin load (name, version, granted capabilities).
- Every configuration change (diff, timestamp, source — CLI, file edit, or MCP-driven if ever exposed).
- Every MCP tool call, inbound and outbound, with its provenance metadata (§5).

This is what makes "who ran what, and why" always an answerable question, whether the "who" is a person, a cron schedule, or an AI agent.
