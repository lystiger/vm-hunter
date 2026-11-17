# EVIDENCE — VMHunter 1.0 Production Reliability Repair

**Date:** 2026-11-17  
**Branch:** `fix/vmhunter-1-0-reliability`  
**Scope:** VMHunter 1.0 (`workflows/claim-oracle-a1.json`) only. VMHunter 2.0 untouched.

---

## 1. Summary of Minimal Code Changes

- [`workflows/claim-oracle-a1.json`](workflows/claim-oracle-a1.json):
  - **`Due for attempt?`**: Tracks `dueChecks` in both daily `metrics.counters` and `data.lifetime`; returns `[]` without mutating `staticData` when blocked/claimed/stopped; preserves 15-minute in-flight lock (`data.nextAttemptAt`) to prevent overlapping executions.
  - **`Cost guard`**:
    - Validates `HUNTER_OCI_A1_OCPUS` / `HUNTER_OCI_A1_MEMORY_GB` fail-closed.
    - Records `knownInstanceIds` before launch.
    - Preserves active `data.rateLimit.currentWaitSeconds` and honors `Retry-After` (seconds or HTTP-date, including > 900s) on transient list failures (`429`, `5xx`, network error).
    - Reconciles `data.pendingInstance` (accepted `PROVISIONING` launch) and `data.pendingLaunch` (network timeout / unknown outcome) via read-only `GET /20160918/instances` before allowing any new `POST` launch:
      - Confirms `lifecycleState === 'RUNNING'` before setting `data.claimed` and emitting `claim` notice.
      - Keeps waiting without duplicate launch while `PROVISIONING` / `STARTING` / `MOVING`.
      - Clears `pendingInstance` and emits `alert` on terminal failure (`TERMINATED` / `TERMINATING` / `NOT_FOUND`) so hunting can resume.
  - **`Launch instance`**: Skips `POST` when `skipLaunch: true` (reconciliation/verification notices). Wraps `this.helpers.httpRequest` in `try/catch` so network timeouts return `statusCode: 0, networkError` cleanly instead of crashing the execution and losing `staticData`.
  - **`Read response`**:
    - Handles network timeout / malformed 200 without `id` as `outcome: 'unknown'`, setting `data.pendingLaunch` for read-only reconciliation on the next run.
    - Persists `data.pendingInstance` on HTTP 200/201 `PROVISIONING` (only marks `data.claimed` immediately if `lifecycleState === 'RUNNING'`).
    - Decays `backoffLevel` (capped at 5) only after 2 consecutive non-429 responses (`non429Streak >= 2`), preventing alternating 500/429 from collapsing backoff to 60s.
    - Parses `Retry-After` as numeric seconds or HTTP-date without capping at 900s.
    - Adds HTTP `403` to configuration/auth stop list (`[400, 401, 403, 404]`).
    - Updates granular daily (`metrics.counters`) and persistent (`data.lifetime`) telemetry.
  - **`Shape summary`**: Resets daily counters at 09:00 while preserving `data.rateLimit` and `data.lifetime`.
- [`scripts/simulate_retry_policy.py`](scripts/simulate_retry_policy.py): Reproducible simulation comparing baseline vs. proposed retry policy.
- [`tests/test_claim_oracle_a1.py`](tests/test_claim_oracle_a1.py) & [`tests/test_claim_oracle_a1_reliability.py`](tests/test_claim_oracle_a1_reliability.py): Updated assertions for new contract + 19 new deterministic tests covering the 18-scenario matrix and 4 holdouts.

---

## 2. Commands Executed & Results

1. **Simulation:** `.venv/bin/python scripts/simulate_retry_policy.py` (mocked OCI rate-limit models; **not** real Oracle capacity):
   - `observed_alternating_500_429` (6h): baseline 182 attempts / 78 HTTP 429 (42.9%) vs. proposed 41 attempts / 18 HTTP 429 (43.9%, p50 interval 528.5s instead of 118s).
   - `sustained_scarcity_bucket_180s` (12h, 180s bucket): baseline 365 attempts / 182 HTTP 429 (49.9%) vs. proposed 145 attempts / 44 HTTP 429 (30.3%).
   - `recovery_after_temp_throttling` (6h, 10x 429 then 500): baseline 125 HTTP 500 vs. proposed 117 HTTP 500 (recovers cleanly).
   - `restart_during_cooldown` (6h, restart at 1h, `Retry-After: 1200`): baseline capped at 945s vs. proposed honored 1245s max interval; 0 duplicate launches.
2. **Full Repository Check:** `./scripts/check` -> **RESULT: PASS**
   - Static workflow checks: **PASS** (4 workflow files)
   - Pytest (`.venv/bin/python -m pytest -q`): **230 passed in 9.80s**
   - Local n8n import (`docker compose`): **PASS** (4 workflow files imported)

---

## 3. Mocked vs. Production Evidence

- **Mocked / Deterministic Evidence:** All 230 pytest tests run Code nodes in Node.js against recorded/synthetic fake-OCID fixtures (`tests/fixtures/oracle/`) with no live OCI or Discord calls. Local n8n import validates workflow JSON against the pinned `n8nio/n8n` Docker image.
- **Production Evidence:** Live LysGPT MCP tools were not accessible in this session; baseline runtime facts come from the 2026-11-17 ~06:19 UTC audit. No live OCI launch or production deployment was performed.

---

## 4. Remaining Risks

- Real OCI capacity in the target AD remains external and scarce; better retry behavior reduces throttling but cannot guarantee capacity allocation.
- Deployed workflow on the owner's VM may differ from `main` (`UNPROVEN` until checked during deployment).
- If `Due for attempt?` and `Shape summary` (09:00) execute in the exact same second in n8n, both read `staticData` at start; keeping `rateLimit` in `data.rateLimit` and `counters` separate minimizes conflict, though n8n's per-workflow staticData is last-writer-wins.
