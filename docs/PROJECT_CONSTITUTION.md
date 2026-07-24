# Project Constitution

*Part of the WatchFlow documentation set — see [`WATCHFLOW.md`](./WATCHFLOW.md) for the full index.*

This is the supreme governing document of WatchFlow. Where any other document — architecture, roadmap, engineering practice — conflicts with this one, this one wins. Everything below is written to change rarely and only through the amendment process in §7.

---

## 1. Purpose of this document

Most technical decisions on WatchFlow are made against a moving target: what's fastest to ship, what a dependency makes convenient, what a user asked for last week. This document exists so that those decisions are made against something that doesn't move — a small set of commitments the project will not trade away for convenience.

Everything here is a constraint on the project, not a feature description. Feature descriptions live in [`ARCHITECTURE.md`](./ARCHITECTURE.md) and [`ROADMAP.md`](./ROADMAP.md).

---

## 2. Mission

**Give developers, DevOps teams, and AI agents a single, small, trustworthy engine for turning "something happened" into "the right work ran" — locally, in CI, and in production — without polling, without shell-injection risk, and without vendor lock-in.**

---

## 3. Problem statement

Event-reactive automation today is split across four unsatisfying camps:

1. **Shell-glue tools** (`entr`, `watchexec`, `nodemon`) — fast and simple, but limited to a single command per invocation, no DAG, no durable history, and easy to make shell-injection-prone in casual use.
2. **Cloud CI/CD platforms** (GitHub Actions, GitLab CI, Argo) — powerful and durable, but heavyweight, network-bound, and slow: feedback loops measured in seconds-to-minutes, not the sub-100ms loop a developer's edit-test cycle demands.
3. **Bespoke scripts** built on ad-hoc watchers — start clean, end tangled. Polling loops creep in, threading bridges leak, blocking I/O sneaks onto the event loop, UI rendering couples to the engine. This is what WatchFlow's own legacy prototype became.
4. **AI agent tooling** — increasingly capable agents need to *do things* on a developer's machine or in a pipeline, but the common answer today is "let the agent shell out," which throws away every safety and audit property a human-run pipeline would insist on.

WatchFlow occupies the gap common to all four: **lightweight like a shell tool, durable and programmable like a platform, disciplined like a safety-critical system, and speakable by both humans and AI agents through the same interface.**

---

## 4. Who WatchFlow serves

- Developers who want their machine to react, not poll.
- DevOps and platform engineers who need a daemon-capable, observable event reactor that fits inside systemd, Docker, or a Kubernetes sidecar.
- CI/CD practitioners who want sub-second local feedback loops that complement, not replace, their cloud pipeline.
- AI agent builders who need a safe, audited execution substrate their agents can call into over MCP instead of shelling out directly.
- Educators and learners who benefit from a small, explicit, async-pure reference architecture.

WatchFlow does **not** primarily serve: teams looking for a hosted, multi-tenant SaaS orchestrator, or teams who need distributed, cluster-scale job scheduling (see Non-goals, §6).

---

## 5. Non-negotiable tenets

These are articles, not suggestions. A change that violates one of these requires a constitutional amendment (§7), not a pull request.

**Article I — Async-first, no blocking compromises.**
No blocking I/O ever executes on the event loop. No `to_thread` fallback as a permanent fixture. No polling loop, anywhere, ever. If a dependency has no async surface, it is wrapped or replaced.

**Article II — Cross-platform parity.**
A Trigger or Workflow authored on Linux behaves identically on macOS and Windows. Platform differences are absorbed inside Source Adapters and the Executor; they never leak into user-facing configuration as platform-specific forks in the common path.

**Article III — Safe execution by default.**
Subprocess execution uses `asyncio.create_subprocess_exec` with `shell=False` and `argv` as `list[str]`, always. There is no configuration flag that turns this off silently; unsafe behavior, if ever offered, is explicit, opt-in, and audit-logged without exception.

**Article IV — Event-driven, never simulated.**
The engine sleeps until something real happens — a kernel notification, a scheduled tick, an inbound request, a tool call. `while True` polling has no place anywhere in the core.

**Article V — Minimal core, extensible edges.**
The core stays small enough to read in an afternoon. Anything domain-specific — webhook parsing, queue brokers, git-provider quirks, notification integrations — is a plugin, not a core dependency.

**Article VI — Local-first, cloud-optional.**
The engine runs fully offline with zero cloud dependency. Cloud sync, team dashboards, and hosted features are opt-in additions layered on top, never a requirement to run WatchFlow at all.

**Article VII — MCP is first-class, not bolted on.**
WatchFlow speaks MCP as both a server and a client at the core layer, not as an optional plugin bought later. An AI agent is a peer caller of the engine, held to the same safety and audit standards as a human or a cron tick.

**Article VIII — Observability is structural.**
Every event, every trigger match, every step execution produces a structured record. No `print()`, no string-formatted logs, no unobserved code paths in the core.

**Article IX — Breaking change requires a major version and an RFC.**
Public API and configuration-schema changes that break existing Workflows require a major version bump and pass through the RFC process once governance reaches the Core Team stage (see [`DECISION_LOG.md`](./DECISION_LOG.md) for the governance timeline).

**Article X — The UI is always a consumer, never a dependency.**
The TUI, and any future web UI, subscribes to the same observability bus every other exporter does. The engine runs headlessly on a server with no terminal at all; nothing in the core assumes a UI is attached.

---

## 6. Non-goals

Stated explicitly so scope creep has something concrete to be measured against:

- **Not a hosted, multi-tenant SaaS.** WatchFlow is software you run, not a service operated on your behalf. (Optional, opt-in cloud sync for teams is a plugin-layer feature, not the product.)
- **Not a cluster-scale distributed job scheduler.** WatchFlow orchestrates on one machine, or a small fleet via explicit daemon connections — it does not aim to replace Kubernetes-native workflow engines at cluster scale.
- **Not a secrets manager.** WatchFlow passes through and scrubs environment variables; it does not store, generate, or rotate secrets.
- **Not a general-purpose AI agent framework.** WatchFlow orchestrates *around* agents and tools via MCP. It does not implement agent reasoning, memory, or planning.
- **Not a replacement for cloud CI/CD.** WatchFlow complements GitHub Actions, GitLab CI, and similar platforms with a fast local/edge loop; it does not aim to replicate their hosted-runner, artifact-storage, or approval-gate feature surface.

---

## 7. Amendment process

Articles in §5 and the non-goals in §6 may only be changed by:

1. Filing an RFC describing the proposed change and its consequences.
2. A 4-week public comment period once governance has reached the RFC + Core Team stage (see [`DECISION_LOG.md`](./DECISION_LOG.md)).
3. Supermajority approval of the Core Team.
4. Recording the change as a dated, numbered entry in [`DECISION_LOG.md`](./DECISION_LOG.md), which supersedes the prior text here.

Before the Core Team stage exists, amendments require the sole maintainer to record the change as an ADR with an explicit rationale — the bar for changing a constitutional article stays high even under a BDFL, precisely because these are the commitments meant to outlast any one maintainer's short-term judgment.

---

## 8. What "good" looks like

WatchFlow is succeeding if:

- A new contributor can read the core engine's source end to end in one sitting and understand the full lifecycle of an event.
- A DevOps team trusts it to run as an unattended daemon for months without a `shell=True` incident.
- An AI agent builder reaches for WatchFlow's MCP surface *instead of* giving their agent raw shell access, because it is safer and gives them an audit trail for free.
- Nobody has ever had to explain why an event was silently dropped.
