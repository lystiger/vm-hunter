# PRETEST — VMHunter 1.0 Production Reliability Repair

**Date:** 2026-11-17  
**Branch:** `fix/vmhunter-1-0-reliability`  
**Target workflow:** `workflows/claim-oracle-a1.json` (VMHunter 1.0 only; VMHunter 2.0 frozen)

---

## 1. Problem Statement

VMHunter 1.0 (`workflows/claim-oracle-a1.json`) is deployed and actively polling Oracle Cloud for an Always Free `VM.Standard.A1.Flex` instance, but has not acquired an instance and exhibits production reliability defects:

1. **Ineffective 429 rate-limit backoff under alternating 500/429 responses** (112 out-of-capacity vs. 52 HTTP 429 responses in the 2026-11-17 06:19 UTC audit window).
2. **`Retry-After` headers exceeding 900s (or HTTP-date format) are truncated or ignored.**
3. **Uncaught network/timeout errors in `Launch instance`** crash the execution, lose static-data updates, leave `nextAttemptAt` stuck at the 15-minute in-flight lock, and fail to reconcile whether OCI actually accepted the launch.
4. **Premature claim success & missing lifecycle verification**: `Read response` treats HTTP 200 (`PROVISIONING`) as immediately claimed without verifying `RUNNING`, and cannot reconcile lost responses.
5. **HTTP 403 is not classified as a configuration/auth stop** (only 400, 401, 404 stop the claimer).
6. **Daily 09:00 summary (`Shape summary`) wipes `consecutive429` and `currentWaitSeconds`** (stored inside `data.metrics`) and discards lifetime telemetry.
7. **Cost guard transient failure overwrites active cooldown** to 60s instead of preserving `currentWaitSeconds` / `Retry-After`.

---

## 2. Baseline Evidence

### Repository & Check Baseline (observed 2026-11-17)
- `./scripts/check` on `main` (`709f9d1`):
  - Static workflow checks: **PASS** (4 workflow files)
  - Pytest suite: **211 passed** in 8.50s
  - Local n8n import (`n8nio/n8n` pinned in `compose.yaml`): **PASS** (4 workflow files imported)

### Runtime Audit Baseline
- Live LysGPT MCP tools (`get_vmhunter_runtime_status`, `get_vmhunter_executions`, `get_vmhunter_claimer_state`, `get_vmhunter_diagnostics`) are **not accessible** from this session's toolset/environment (`LYSGPT_VMHUNTER_N8N_URL` / `LYSGPT_VMHUNTER_N8N_API_KEY` not present in shell env or `.env`).
- Baseline relies on the **2026-11-17 ~06:19 UTC** read-only LysGPT audit:
  - n8n API reachable; claimer active, `state=waiting`, `stopped=false`, `claimed=false`, no claimed OCID.
  - Persisted counter-period: 164 attempts, 112 out-of-capacity (HTTP 500), 52 rate-limited (HTTP 429).
  - Latest 20 executions: 7 launch attempts (4x HTTP 500 `Out of host capacity`, 3x HTTP 429 `TooManyRequests`), 13 schedule checks without launch.
  - All 7 launch attempts passed the cost guard; `opc-request-id` present.

---

## 3. Confirmed Defects vs. Unresolved Hypotheses

### Confirmed Defects (verified in `workflows/claim-oracle-a1.json`)
- **D-A1 (Backoff reset on 500):** `Read response` initializes `let consecutive429 = 0;` and only increments it on `statusCode === 429`. Any HTTP 500 `Out of host capacity` resets `consecutive429` to `0` and `wait` to `60`.
- **D-A2 (`Retry-After` cap & format):** `wait = Math.min(900, Math.max(60 * 2 ** (consecutive429 - 1), retryAfter))` caps `Retry-After` at 900s and fails to parse HTTP-date `Retry-After` values (`Number(...)` -> `NaN`).
- **D-B1 (Uncaught `Launch instance` error):** `Launch instance` calls `this.helpers.httpRequest(...)` without `try/catch`. A timeout or socket reset throws, skipping `Read response`, leaving `nextAttemptAt` at `+15m`, and losing static-data changes in n8n.
- **D-B2 (Daily reset wipes backoff & lifetime totals):** `Shape summary` sets `data.metrics = { day: today, counters: {}, alerted: metrics.alerted ?? {} }`, deleting `metrics.consecutive429`, `metrics.currentWaitSeconds`, and all attempt totals.
- **D-B3 (Missing granular telemetry):** `counters` conflates schedule checks, due checks, cost-guard checks, submitted launches, responses received, timeouts, accepted launches, and verified `RUNNING` instances.
- **D-C1 (No `RUNNING` verification / reconciliation):** `Read response` sets `data.claimed` on HTTP 200 (`PROVISIONING`) without polling `GET /20160918/instances` / `GET /20160918/instances/<id>` to confirm `RUNNING` or handle terminal failure (`TERMINATING`/`TERMINATED`). No reconciliation exists for timed-out launches whose response was lost.
- **D-E1 (HTTP 403 not stopped):** `Read response` only stops on `[400, 401, 404]`, missing `403`.
- **D-E2 (Cost guard transient cooldown override):** On transient list failure, `Cost guard` sets `data.nextAttemptAt = Date.now() + 60 * 1000`, shortening any active 429 cooldown.

### Unresolved Hypotheses (cannot be proven without live OCI / n8n runtime)
- **H-1:** OCI's true rate-limit window/bucket behavior in the owner's home region (whether 429s are triggered by concurrent compartment/tenancy calls or sustained ~1 req/min).
- **H-2:** Whether the deployed n8n workflow on the VM has any local edits diverging from `main` (`709f9d1`) — marked `UNPROVEN` until live n8n diff is checked.
- **H-3:** Whether Oracle A1 capacity in the target AD will become available within any specific time window.

---

## 4. Falsifiable Acceptance Criteria

| ID | Criterion | Pass Condition |
|---|---|---|
| **AC-1** | Alternating 500/429 throttling protection | Under alternating 500/429 responses, `consecutive429` does not reset to 0 on a single 500; backoff escalates and simulation shows fewer 429s than baseline without permanent stop. |
| **AC-2** | `Retry-After` > 15 min & HTTP-date | Both numeric `Retry-After > 900` and HTTP-date `Retry-After` are parsed and respected (`nextAttemptAt >= now + retryAfter * 1000`). |
| **AC-3** | HTTP 400 / 401 / 403 / 404 handling | All four stop the claimer (`data.stopped = true`), increment `configErrors`, and emit `failure` notice. |
| **AC-4** | Cost guard fail-closed & transient safety | Cost guard refuses (stops) on over-limit or non-transient list failure; on transient list failure it skips launch without stopping and preserves active cooldown (`>= currentWaitSeconds`, honoring `Retry-After` if 429). |
| **AC-5** | Network timeout / lost response safety | `Launch instance` catches network errors and marks outcome `unknown` (`pendingLaunch`); subsequent run reconciles via read-only OCI `GET` before any new `POST` launch. |
| **AC-6** | Launch verification (`PROVISIONING` -> `RUNNING`) | HTTP 200 persists `pendingInstance` (no duplicate launch), polls read-only instance status, marks `claimed` + emits `claim` only on `RUNNING`, and recovers safely on `TERMINATED`/`TERMINATING`. |
| **AC-7** | Telemetry & daily reset preservation | Daily 09:00 summary resets daily counters, preserves `rateLimit` backoff state, and preserves `lifetime` counters. |
| **AC-8** | State persistence & overlap safety | Cooldown (`nextAttemptAt`) survives simulated n8n restart; overlapping scheduler ticks while locked or pending/claimed produce at most 1 launch. |
| **AC-9** | Simulation comparison | Reproducible simulation compares baseline vs. proposed policy on observed 500/429 pattern + synthetic patterns (sustained scarcity, burst throttling, recovery, restart). |
| **AC-10** | Repo gates | `./scripts/check` (static checks, pytest including all 18 matrix cases + holdouts, local n8n import) passes with 0 live OCI/Discord calls and no secrets. |

---

## 5. Test Matrix & Holdouts

### Mandatory 18-Scenario Deterministic Matrix
1. HTTP 500 `Out of host capacity` -> retry after delay, `outOfCapacity` incremented.
2. HTTP 429 -> cooldown respected, `rateLimited` incremented.
3. Alternating 500 / 429 -> throttling protection remains effective (backoff does not collapse to 60s).
4. Consecutive 429 -> exponential backoff increases (`60 -> 120 -> 240 -> 480 -> 900`).
5. `Retry-After` > 15 minutes (e.g. `1200`s and HTTP-date) -> server guidance respected (> 900s).
6. HTTP 401 / 403 -> safe auth/config stop (`stopped=true`, `failure` notice).
7. HTTP 400 -> config failure stop (`stopped=true`, `failure` notice).
8. Cost guard refusal -> no launch, `stopped=true`, `costGuardRefusals` incremented.
9. Cost guard transient failure -> safe retry without launch, preserves active cooldown.
10. Network timeout before response -> outcome marked `unknown`, `pendingLaunch` set, no crash.
11. Accepted launch, response lost -> next run reconciles via read-only `GET` and detects existing instance without duplicate launch.
12. Valid accepted launch response (`PROVISIONING`) -> persists `pendingInstance.id`, does not yet report `RUNNING` claim.
13. Instance remains `PROVISIONING` -> no duplicate launch, continues read-only polling.
14. Instance reaches `RUNNING` -> `claimed` persisted, `verifiedRunning` incremented, `claim` notice emitted.
15. Instance reaches terminal failure (`TERMINATED` / `TERMINATING`) -> `pendingInstance` cleared, safe failure recorded, hunting can resume.
16. n8n restart during cooldown -> `nextAttemptAt` + `rateLimit` state survive and block early launch.
17. Overlapping scheduler executions -> at most one intended launch.
18. Daily counter reset -> daily counters reset, `lifetime` telemetry and `rateLimit` state preserved.

### Holdout Scenarios (for independent TEST verification)
- **H-1 (Multi-page reconciliation):** `pendingLaunch` reconciliation when compartment has >1 page of instances (`opc-next-page`) and the newly created instance is on page 2.
- **H-2 (429 during Cost Guard while `pendingInstance` is active):** Cost guard / reconciliation hits 429 with `Retry-After: 1800`; must not clear `pendingInstance` and must honor 1800s wait.
- **H-3 (09:00 summary race during active backoff):** `Shape summary` runs while `consecutive429 = 4` (`currentWaitSeconds = 480`); `Due for attempt?` immediately after 09:00 must still see `consecutive429 = 4` and `nextAttemptAt` intact.
- **H-4 (Malformed/empty 200 body):** Launch returns HTTP 200 with `{}` (missing `id`); must NOT mark claimed and must treat as unknown/reconcile rather than crashing.
