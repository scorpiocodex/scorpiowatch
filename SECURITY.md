# Security Policy

ScorpioWatch executes local commands in reaction to events that can, in several
configurations, originate outside the machine it runs on. We take the security of
that execution path seriously. This document describes how to report a
vulnerability and what to expect in return.

For the full threat model, trust boundaries, and subprocess/plugin/MCP safety
guarantees, see [`docs/SECURITY_MODEL.md`](./docs/SECURITY_MODEL.md).

## Supported versions

ScorpioWatch is in its pre-release band (`v0.1.0` → `v0.3.2`) and carries **no public
stability guarantees** yet (see [`docs/ROADMAP.md`](./docs/ROADMAP.md)). Security
fixes are applied to the latest pre-release. Formal LTS support windows begin at
`v1.0.0`.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately through **GitHub Security Advisories**:

1. Go to the repository's **Security** tab.
2. Select **Report a vulnerability** to open a private advisory.
3. Include a description, affected version/commit, reproduction steps, and impact.

Private disclosure gives us time to release a fix before details become public.

## What to expect

| Stage | Commitment |
|---|---|
| **Acknowledgment** | Within **48 hours** of your report |
| **Fix — critical** | Within **7 days** |
| **Fix — high** | Within **30 days** |
| **Fix — medium / low** | Within **90 days** |

We will keep you informed of progress throughout, coordinate a disclosure timeline
with you, and credit you in the advisory unless you ask us not to.

## Scope

In scope: the ScorpioWatch engine, core adapters, MCP gateway, and first-party
plugins in this repository. Explicitly out of scope (per
[`docs/SECURITY_MODEL.md`](./docs/SECURITY_MODEL.md) §1): protecting against a
fully compromised host OS, or against a user with root/administrator access to the
machine ScorpioWatch runs on.
