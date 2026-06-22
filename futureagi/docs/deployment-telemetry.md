# Deployment Telemetry

Self-hosted FutureAGI instances phone home to FutureAGI so we understand how
the open-source product is deployed and used, and so we can reach out to
operators about their deployment. This page documents exactly what is sent,
how to opt out, and how the data is protected.

## What is sent

Telemetry has two messages, both posted to `FUTURE_AGI_TELEMETRY_URL`
(default `https://api.futureagi.com`):

### 1. Registration (once, and whenever the mode changes)

| Field | Example | Notes |
| --- | --- | --- |
| `instance_id` | a UUID | Stable per instance, generated on first start |
| `version` | `1.8.20` | Software version |
| `deployment_type` | `docker` / `kubernetes` / `bare_metal` | |
| `timestamp` | ISO-8601 | |
| `telemetry_disabled` | `false` | Whether full telemetry is opted out |
| `users` | `[{"email": "...", "domain": "..."}]` | **Only when telemetry is enabled.** Active users (capped at 500), so we can contact your deployment. May be added to our CRM. |

### 2. Heartbeat (periodically, every `FUTURE_AGI_TELEMETRY_INTERVAL_HOURS`)

Usage **counts only** for the previous fixed window — traces, spans,
projects, evaluations, simulations, experiments, gateway requests, datasets,
and active users. **No usage content** (no trace bodies, eval inputs/outputs,
prompts, or payloads) is ever transmitted. A count may be `null` when its
collector could not run, so "unknown" is distinguishable from a genuine zero.

Counts for traces and spans are read from the ClickHouse `spans` table (the
post-CH-25 source of truth), not the legacy Postgres tables.

## Opting out

Set `FUTURE_AGI_TELEMETRY_DISABLED=true`. In this mode:

- A single **minimal** registration ping is still sent: `instance_id`,
  `version`, `deployment_type` — **no emails, no usage data**.
- No heartbeats are sent.

To send nothing at all, block outbound traffic to `FUTURE_AGI_TELEMETRY_URL`
at your network edge.

A disclosure line is also logged at startup (`deployment_telemetry_disclosure`)
stating the active mode and what will be sent.

## How heartbeats are authenticated

On the first registration the receiver mints a per-instance secret and returns
it once. The instance persists it and signs every heartbeat body with an
HMAC-SHA256 (`X-FAGI-Telemetry-Signature` header). This is trust-on-first-use:
it prevents anyone else from posting heartbeats for your `instance_id`. The
secret is a random capability token, not a credential tied to any user.

## Reliability

- Telemetry never blocks or fails a product/user flow.
- A failed heartbeat is buffered to disk
  (`FUTURE_AGI_TELEMETRY_BUFFER_DIR`) and retried on the next cycle, oldest
  first, with a 30-day retention.

## Configuration reference

| Env var | Default | Purpose |
| --- | --- | --- |
| `FUTURE_AGI_TELEMETRY_DISABLED` | `false` | Opt out of full telemetry |
| `FUTURE_AGI_TELEMETRY_INTERVAL_HOURS` | `6` | Heartbeat interval (1,2,3,4,6,8,12,24) |
| `FUTURE_AGI_TELEMETRY_JITTER_SECONDS` | `1800` | Random delay to spread load |
| `FUTURE_AGI_TELEMETRY_URL` | `https://api.futureagi.com` | Receiver endpoint |
| `FUTURE_AGI_TELEMETRY_TIMEOUT_SECONDS` | `5` | HTTP timeout |
| `FUTURE_AGI_TELEMETRY_BUFFER_DIR` | `/tmp/futureagi-deployment-telemetry` | Failed-heartbeat buffer |
