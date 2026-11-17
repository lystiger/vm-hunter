#!/usr/bin/env python3
"""Reproducible retry-policy simulation comparing VMHunter 1.0 baseline vs. proposed policy.

Explicitly models an external mocked OCI rate limiter (token bucket / sliding window) and
replays both the observed 500/429 pattern from the 2026-11-17 audit and synthetic patterns.
Simulated outcomes measure client retry behavior under a defined model; they do NOT predict
real Oracle Cloud A1 capacity availability.
"""
from __future__ import annotations

import json
import random
import statistics
from dataclasses import dataclass, field


def parse_retry_after(value: str | int | float | None) -> int:
    if value is None or value == "":
        return 0
    try:
        n = float(value)
        return max(0, int(n)) if n == n else 0
    except (TypeError, ValueError):
        return 0


@dataclass
class SimResult:
    policy: str
    scenario: str
    duration_hours: float
    total_attempts: int
    total_500: int
    total_429: int
    cooldown_seconds: float
    intervals_sec: list[float] = field(default_factory=list)
    duplicate_launches: int = 0

    def summary(self) -> dict:
        ivs = sorted(self.intervals_sec)
        p50 = statistics.median(ivs) if ivs else 0
        p95 = ivs[int(0.95 * (len(ivs) - 1))] if ivs else 0
        return {
            "policy": self.policy,
            "scenario": self.scenario,
            "attempts": self.total_attempts,
            "http_500": self.total_500,
            "http_429": self.total_429,
            "rate_429_pct": round(100.0 * self.total_429 / max(1, self.total_attempts), 1),
            "cooldown_hours": round(self.cooldown_seconds / 3600.0, 2),
            "interval_min_s": round(min(ivs), 1) if ivs else 0,
            "interval_p50_s": round(p50, 1),
            "interval_p95_s": round(p95, 1),
            "interval_max_s": round(max(ivs), 1) if ivs else 0,
            "duplicate_launches": self.duplicate_launches,
        }


def step_baseline(state: dict, status: int, retry_after: int = 0) -> int:
    c429 = state.get("consecutive429", 0)
    if status == 429:
        c429 += 1
        wait = min(900, max(60 * (2 ** (c429 - 1)), retry_after))
    else:
        c429 = 0
        wait = 60
    state["consecutive429"] = c429
    state["currentWaitSeconds"] = wait
    return wait


def step_proposed(state: dict, status: int, retry_after: int = 0) -> int:
    c429 = state.get("consecutive429", 0)
    level = state.get("backoffLevel", 0)
    non429 = state.get("non429Streak", 0)
    if status == 429:
        c429 += 1
        level = min(5, level + 1)
        non429 = 0
        backoff = min(900, 60 * (2 ** (level - 1)))
        wait = max(backoff, retry_after)
    else:
        c429 = 0
        non429 += 1
        if non429 >= 2 and level > 0:
            level -= 1
            non429 = 0
        # Gentle cooldown floor while recent 429 pressure is still active
        wait = 60 if level <= 1 else min(900, 60 * (2 ** (level - 2)))
    state["consecutive429"] = c429
    state["backoffLevel"] = level
    state["non429Streak"] = non429
    state["currentWaitSeconds"] = wait
    return wait


def run_sim(
    policy_name: str,
    step_fn,
    scenario_name: str,
    duration_sec: int,
    responder,
    seed: int = 42,
    restart_at_sec: int | None = None,
) -> SimResult:
    rng = random.Random(seed)
    t = 0
    next_attempt_at = 0
    state: dict = {}
    res = SimResult(policy=policy_name, scenario=scenario_name, duration_hours=duration_sec / 3600.0,
                    total_attempts=0, total_500=0, total_429=0, cooldown_seconds=0.0)
    last_attempt_t = None

    # Scheduler ticks every 60s
    while t < duration_sec:
        if restart_at_sec is not None and t == restart_at_sec:
            # Simulate n8n restart: staticData persisted on disk survives; in-memory vars do not
            state = json.loads(json.dumps(state))
        if t >= next_attempt_at:
            jitter = rng.randint(0, 30)
            exec_t = t + jitter
            if exec_t >= duration_sec:
                break
            if last_attempt_t is not None:
                res.intervals_sec.append(exec_t - last_attempt_t)
            last_attempt_t = exec_t
            status, retry_after = responder(exec_t, res.total_attempts)
            res.total_attempts += 1
            if status == 429:
                res.total_429 += 1
            elif status == 500:
                res.total_500 += 1
            wait = step_fn(state, status, retry_after)
            res.cooldown_seconds += wait
            next_attempt_at = exec_t + wait
        t += 60
    return res


def main() -> None:
    # 1. Observed audit pattern replay: 7-launch sequence [500, 429, 500, 429, 500, 429, 500] repeated
    audit_seq = [500, 429, 500, 429, 500, 429, 500]
    scenarios = [
        ("observed_alternating_500_429", 6 * 3600, lambda t, i: (audit_seq[i % len(audit_seq)], 0), None),
        # 2. Explicit token-bucket / window rate limiter:
        #    Allows 1 request per 180s; faster requests return 429 with Retry-After=180
        ("sustained_scarcity_bucket_180s", 12 * 3600, None, None),
        # 3. Temporary throttling burst (first 20 attempts 429, then 500 Out of host capacity)
        ("recovery_after_temp_throttling", 6 * 3600, lambda t, i: (429 if i < 10 else 500, 0), None),
        # 4. Restart during cooldown at t=3600s under 180s bucket
        ("restart_during_cooldown", 6 * 3600, lambda t, i: (429 if (i % 2 == 1) else 500, 1200 if i == 3 else 0), 3600),
    ]

    results = []
    for name, dur, resp, restart_at in scenarios:
        for pname, fn in (("baseline", step_baseline), ("proposed", step_proposed)):
            if name == "sustained_scarcity_bucket_180s":
                last_ok = [-9999]

                def bucket_resp(exec_t, _i, _last=last_ok):
                    if exec_t - _last[0] < 180:
                        return 429, 180 - (exec_t - _last[0])
                    _last[0] = exec_t
                    return 500, 0

                r = run_sim(pname, fn, name, dur, bucket_resp, restart_at_sec=restart_at)
            else:
                r = run_sim(pname, fn, name, dur, resp, restart_at_sec=restart_at)
            results.append(r.summary())

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
