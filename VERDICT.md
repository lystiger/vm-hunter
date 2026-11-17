# VERDICT — VMHunter 1.0 Production Reliability Repair

**Branch:** `fix/vmhunter-1-0-reliability` (diff vs `main` at `709f9d1`)  
**Target:** `/home/lystiger/Projects/vm-hunter` (`workflows/claim-oracle-a1.json` only; VMHunter 2.0 untouched)  
**Verifier:** Independent `pro` research agent (adversarial review)  
**Overall Verdict:** `PASS`

**Summary:** All round-1 blocking and edge-case findings in workflow code and tests were addressed and independently verified. Static workflow checks (`4 workflow file(s)`) and all `230` pytest tests pass cleanly on the updated branch, direct Node.js probes confirm every newly introduced fix, and `EVIDENCE.md` records `./scripts/check -> RESULT: PASS` (including local `n8nio/n8n:1.123.87` Docker import).

---

## Independent Verification Executed

- `.venv/bin/python -m pytest -q` → `230 passed in 8.86s` (exit `0`, 0 live OCI/Discord calls)
- `.venv/bin/python scripts/check_workflows.py` → `PASS: 4 workflow file(s) checked` (exit `0`)
- Context7 lookup (`/n8n-io/n8n-docs`): verified `this.helpers.httpRequest` `timeout` option is in milliseconds (`timeout: 30000` = 30s).
- Independent Node.js probes against updated `workflows/claim-oracle-a1.json` verified:
  1. `Due for attempt?` returns `[]` without mutating `staticData` when `claimed`, `stopped`, or `nextAttemptAt > now`.
  2. `Cost guard` 429 increments `rateLimited: 1` without incrementing `errors`.
  3. `Cost guard` successful `PROVISIONING` poll resets `rateLimit.currentWaitSeconds` from `1800` to `60` and sets `nextAttemptAt = now + 60_000`.
  4. `Read response` `capReached` alert after alternating 500/429 reports `"Rate limited (HTTP 429, backoff level 5/5). Waiting 900s before next attempt."`.
- Local n8n Docker import (`./scripts/check` step 3): verified via `EVIDENCE.md` post-edit `./scripts/check` run (`RESULT: PASS`, 4 workflow files imported).

---

## Per-Criterion Verdict (AC-1 through AC-10)

| ID | Criterion | Verdict | Evidence & Findings |
|---|---|---|---|
| **AC-1** | Alternating 500/429 throttling protection | `PASS` | `Read response` preserves `backoffLevel` until `non429Streak >= 2`, escalating alternating 500/429 waits `60 -> 120 -> 240 -> 480 -> 900`. Simulation reduces 429s vs baseline without permanent stop. |
| **AC-2** | `Retry-After` > 15 min & HTTP-date | `PASS` | `parseRetryAfter()` in `Cost guard` and `Read response` parses numeric seconds and HTTP-dates without 900s cap; `headers: null` guarded. |
| **AC-3** | HTTP 400 / 401 / 403 / 404 handling | `PASS` | `Read response` stops (`data.stopped = true`), increments `configErrors`, and emits `failure` on `[400, 401, 403, 404]`. `Cost guard` stops fail-closed on 403. |
| **AC-4** | Cost guard fail-closed & transient safety | `PASS` | Validates `HUNTER_OCI_A1_OCPUS` / `HUNTER_OCI_A1_MEMORY_GB` fail-closed, uses `timeout: 30000`, preserves `currentWaitSeconds` / `Retry-After` on transient failures, keeps `rateLimited` vs `errors` mutually exclusive. |
| **AC-5** | Network timeout / lost response safety | `PASS` | `Launch instance` uses `timeout: 30000` and `try/catch`; `Read response` marks `outcome: 'unknown'` and sets `data.pendingLaunch`; `Cost guard` reconciles via read-only `GET /20160918/instances` before any `POST`. |
| **AC-6** | Launch verification (`PROVISIONING` -> `RUNNING`) | `PASS` | `Read response` persists `data.pendingInstance` unless `RUNNING`; `Cost guard` polls read-only `GET`, emits `claim` only on `RUNNING`, clears `pendingInstance` + emits `alert` on `TERMINATED`/`TERMINATING`/`NOT_FOUND`. |
| **AC-7** | Telemetry & daily reset preservation | `PASS` | `Due for attempt?` avoids idle-tick `staticData` writes; `Shape summary` resets `metrics.counters` while preserving `data.rateLimit` and `data.lifetime`. |
| **AC-8** | State persistence & overlap safety | `PASS` | `nextAttemptAt` and `rateLimit` survive JSON serialization; `Due for attempt?` holds 15-minute lock; `timeout: 30000` bounds hung HTTP calls. |
| **AC-9** | Simulation comparison | `PASS` | `scripts/simulate_retry_policy.py` compares `baseline` vs `proposed` across 4 mocked patterns. |
| **AC-10** | Repo gates | `PASS` | Static check (`PASS: 4 workflow file(s) checked`), pytest (`230 passed`), and local `n8nio/n8n:1.123.87` import (`RESULT: PASS`) all pass with 0 live calls and no secrets. |

---

## Remaining Non-Blocking Risks & Caveats

- **n8n `staticData` last-writer-wins window:** An active execution sleeping in `Jitter` across `09:00:00` can still overlap `Daily 09:00`, and multi-call slowdowns exceeding 60s before `staticData` commits remain a narrow platform caveat.
- **Live runtime parity:** Live OCI capacity behavior and parity with the workflow currently deployed on the owner's VM remain `UNPROVEN` until Lystiger inspects/deploys on the VM.
