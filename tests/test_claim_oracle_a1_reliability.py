"""Mandatory 18-scenario reliability test matrix and holdout tests for VMHunter 1.0."""
import json
from datetime import datetime, timezone
from pathlib import Path

from test_claim_oracle_a1 import (
    AD,
    COMPARTMENT,
    ENV,
    LAUNCH,
    LIST_BLOCK,
    LIST_BOOT,
    LIST_INSTANCES,
    NOW,
    REQUEST,
    a1,
    first_json,
    guard_routes,
    key,
    node_code,
    page,
    respond,
    respond_raw,
    run,
)


def _run_full_tick(run, static, routes, env=None, now=NOW):
    """Simulate one full scheduler execution: Due -> Cost guard -> Launch -> Read response."""
    due_res = run(node_code("Due for attempt?"), static=static, env=env, now=now)
    static = due_res["staticData"]
    if not due_res["result"]:
        return {"ran": False, "staticData": static, "requests": []}

    cg_res = run(node_code("Cost guard"), static=static, routes=routes, env=env, now=now)
    static = cg_res["staticData"]
    all_reqs = list(cg_res["requests"])
    if not cg_res["result"]:
        return {"ran": True, "stage": "cost_guard_skip", "staticData": static, "requests": all_reqs}

    cg_out = first_json(cg_res)
    if cg_out.get("kind") == "failure":
        return {"ran": True, "stage": "cost_guard_refused", "notice": cg_out, "staticData": static, "requests": all_reqs}

    launch_res = run(node_code("Launch instance"), input=cg_out, static=static, routes=routes, env=env, now=now)
    all_reqs.extend(launch_res["requests"])
    rr_res = run(node_code("Read response"), input=first_json(launch_res), static=static, env=env, now=now)
    return {
        "ran": True,
        "stage": "complete",
        "notice": first_json(rr_res),
        "staticData": rr_res["staticData"],
        "requests": all_reqs,
    }


def test_matrix_03_alternating_500_429_preserves_backoff(run):
    static = {}
    waits_on_429 = []
    for i in range(6):
        if i % 2 == 0:
            res = respond_raw(run, static, 429, {}, {"code": "TooManyRequests", "message": "Too many"})
            waits_on_429.append(res["staticData"]["nextAttemptAt"] - NOW)
        else:
            res = respond(run, static, "launch-500-out-of-capacity.json")
        static = res["staticData"]
    assert waits_on_429 == [60_000, 120_000, 240_000]


def test_matrix_05_retry_after_http_date_and_above_15m(run):
    future_iso = datetime.fromtimestamp((NOW + 1800_000) / 1000, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    res = respond_raw(run, {}, 429, {"retry-after": future_iso}, {"code": "TooManyRequests", "message": "Too many"})
    assert res["staticData"]["nextAttemptAt"] - NOW == 1_800_000


def test_matrix_06_http_403_stops_claimer(run):
    res = respond_raw(run, {}, 403, {"opc-request-id": "REQ-403"}, {"code": "NotAuthorizedOrNotFound", "message": "Forbidden"})
    out = first_json(res)
    assert out["outcome"] == "config"
    assert out["kind"] == "failure"
    assert res["staticData"]["stopped"] is True
    assert res["staticData"]["metrics"]["counters"]["configErrors"] == 1


def test_matrix_09_cost_guard_transient_preserves_active_cooldown(run):
    static = {"rateLimit": {"consecutive429": 3, "backoffLevel": 3, "non429Streak": 0, "currentWaitSeconds": 240}}
    routes = guard_routes()
    routes[LIST_BLOCK] = {"status": 500, "headers": {}, "body": {"code": "InternalError", "message": "oops"}}
    res = run(node_code("Cost guard"), static=static, routes=routes, now=NOW)
    assert res["result"] == []
    assert "stopped" not in res["staticData"]
    assert res["staticData"]["nextAttemptAt"] - NOW == 240_000


def test_matrix_10_network_timeout_before_response_marks_unknown(run):
    routes = guard_routes()  # no LAUNCH route -> Launch instance throws network error
    tick = _run_full_tick(run, {}, routes, now=NOW)
    assert tick["stage"] == "complete"
    assert tick["notice"]["outcome"] == "unknown"
    assert "pendingLaunch" in tick["staticData"]
    assert tick["staticData"]["metrics"]["counters"]["timeouts"] == 1
    assert "claimed" not in tick["staticData"]


def test_matrix_11_accepted_launch_lost_response_reconciles_without_duplicate_launch(run):
    # Tick 1: Launch times out (response lost)
    tick1 = _run_full_tick(run, {}, guard_routes(), now=NOW)
    static = tick1["staticData"]
    assert "pendingLaunch" in static

    # Tick 2: OCI actually created the instance (PROVISIONING). Must reconcile via GET and NOT POST!
    routes2 = guard_routes(instances=page([a1(4, 24, state="PROVISIONING")]))
    tick2 = _run_full_tick(run, static, routes2, now=NOW + 120_000)
    assert all(r["method"] == "GET" for r in tick2["requests"])
    assert "pendingLaunch" not in tick2["staticData"]
    assert tick2["staticData"]["pendingInstance"]["id"] == "ocid1.instance.oc1..example"

    # Tick 3: Instance reaches RUNNING -> verified claim, zero POSTs!
    routes3 = guard_routes(instances=page([a1(4, 24, state="RUNNING")]))
    tick3 = _run_full_tick(run, tick2["staticData"], routes3, now=NOW + 180_000)
    assert all(r["method"] == "GET" for r in tick3["requests"])
    assert tick3["notice"]["kind"] == "claim"
    assert tick3["staticData"]["claimed"]["lifecycleState"] == "RUNNING"


def test_matrix_12_13_14_provisioning_to_running_lifecycle(run):
    # Accepted as PROVISIONING -> no claim notice yet
    routes1 = guard_routes()
    routes1[LAUNCH] = json.loads((Path(__file__).parent / "fixtures/oracle/launch-200-instance.json").read_text())
    tick1 = _run_full_tick(run, {}, routes1, now=NOW)
    assert tick1["notice"]["outcome"] == "accepted"
    assert tick1["notice"]["kind"] == ""
    assert "claimed" not in tick1["staticData"]
    assert "pendingInstance" in tick1["staticData"]

    # Still PROVISIONING -> no duplicate launch
    routes2 = guard_routes(instances=page([a1(4, 24, state="PROVISIONING")]))
    tick2 = _run_full_tick(run, tick1["staticData"], routes2, now=NOW + 60_000)
    assert all(r["method"] == "GET" for r in tick2["requests"])
    assert "pendingInstance" in tick2["staticData"]
    assert "claimed" not in tick2["staticData"]

    # Reaches RUNNING -> verified success
    routes3 = guard_routes(instances=page([a1(4, 24, state="RUNNING")]))
    tick3 = _run_full_tick(run, tick2["staticData"], routes3, now=NOW + 120_000)
    assert tick3["notice"]["kind"] == "claim"
    assert tick3["staticData"]["claimed"]["id"] == "ocid1.instance.oc1..example"
    assert tick3["staticData"]["metrics"]["counters"]["verifiedRunning"] == 1


def test_matrix_15_terminal_failure_clears_pending_and_resumes_hunt(run):
    static = {"pendingInstance": {"id": "ocid1.instance.oc1..example", "acceptedAt": "2026-11-17T06:00:00Z"}}
    routes = guard_routes(instances=page([a1(4, 24, state="TERMINATED")]))
    tick = _run_full_tick(run, static, routes, now=NOW)
    assert tick["notice"]["outcome"] == "terminal_failure"
    assert tick["notice"]["kind"] == "alert"
    assert "pendingInstance" not in tick["staticData"]
    assert "claimed" not in tick["staticData"]
    assert tick["staticData"]["metrics"]["counters"]["terminalFailures"] == 1


def test_matrix_16_n8n_restart_during_cooldown_survives(run):
    res = respond_raw(run, {}, 429, {"retry-after": "300"}, {"code": "TooManyRequests", "message": "Too many"})
    persisted = json.loads(json.dumps(res["staticData"]))  # simulate DB serialize/deserialize on restart
    assert run(node_code("Due for attempt?"), static=persisted, now=NOW + 299_000)["result"] == []
    assert run(node_code("Due for attempt?"), static=persisted, now=NOW + 300_000)["result"] == [{"json": {"due": True}}]


def test_matrix_17_overlapping_scheduler_executions_at_most_one_launch(run):
    t1 = run(node_code("Due for attempt?"), static={}, now=NOW)
    t2 = run(node_code("Due for attempt?"), static=t1["staticData"], now=NOW + 1_000)
    assert t1["result"] == [{"json": {"due": True}}]
    assert t2["result"] == []


def test_matrix_18_daily_reset_preserves_lifetime_and_rate_limit(run):
    static = respond_raw(run, {}, 429, {"retry-after": "240"}, {"code": "TooManyRequests", "message": "Too many"})["staticData"]
    summary = run(node_code("Shape summary"), static=static, now=NOW)
    after = summary["staticData"]
    assert after["metrics"]["counters"] == {}
    assert after["rateLimit"]["consecutive429"] == 1
    assert after["rateLimit"]["currentWaitSeconds"] == 240
    assert after["lifetime"]["attempts"] == 1
    assert after["lifetime"]["rateLimited"] == 1


# ---- Holdout Scenarios (H-1 to H-4) -----------------------------------------------------------

def test_holdout_h1_multipage_reconciliation(run):
    static = {"pendingLaunch": {"submittedAt": "2026-11-17T06:00:00Z", "knownInstanceIds": ["ocid1.instance.oc1..other"]}}
    other = dict(a1(2, 12, state="RUNNING"))
    other["id"] = "ocid1.instance.oc1..other"
    new_inst = a1(2, 12, state="PROVISIONING")
    routes = {
        LIST_INSTANCES: page([other], next_page="p2"),
        f"{LIST_INSTANCES}&page=p2": page([new_inst]),
    }
    tick = _run_full_tick(run, static, routes, now=NOW)
    assert all(r["method"] == "GET" for r in tick["requests"])
    assert tick["staticData"]["pendingInstance"]["id"] == new_inst["id"]


def test_holdout_h2_429_during_pending_instance_verification(run):
    static = {"pendingInstance": {"id": "ocid1.instance.oc1..example", "acceptedAt": "2026-11-17T06:00:00Z"}}
    routes = {LIST_INSTANCES: {"status": 429, "headers": {"retry-after": "1800"}, "body": {"code": "TooManyRequests", "message": "Too many"}}}
    tick = _run_full_tick(run, static, routes, now=NOW)
    assert tick["stage"] == "cost_guard_skip"
    assert "pendingInstance" in tick["staticData"]
    assert tick["staticData"]["nextAttemptAt"] - NOW == 1_800_000


def test_holdout_h3_daily_summary_during_active_backoff(run):
    static = {"nextAttemptAt": NOW + 480_000, "rateLimit": {"consecutive429": 4, "backoffLevel": 4, "non429Streak": 0, "currentWaitSeconds": 480}, "metrics": {"day": "2026-11-16", "counters": {"attempts": 10}, "alerted": {}}}
    after_summary = run(node_code("Shape summary"), static=static, now=NOW)["staticData"]
    assert after_summary["rateLimit"]["consecutive429"] == 4
    assert run(node_code("Due for attempt?"), static=after_summary, now=NOW + 60_000)["result"] == []


def test_holdout_h4_malformed_200_without_id_reconciles_safely(run):
    res = respond_raw(run, {}, 200, {}, {})
    assert first_json(res)["outcome"] == "unknown"
    assert "pendingLaunch" in res["staticData"]
    assert "claimed" not in res["staticData"]


def test_invalid_ocpus_refuses_fail_closed(run):
    res = run(node_code("Cost guard"), routes=guard_routes(), env={"HUNTER_OCI_A1_OCPUS": "-1"})
    assert first_json(res)["kind"] == "failure"
    assert res["staticData"]["stopped"] is True


def test_pending_launch_negative_reconcile_resumes_hunt(run):
    static = {"pendingLaunch": {"submittedAt": "2026-11-17T06:00:00Z", "knownInstanceIds": []}}
    tick = _run_full_tick(run, static, guard_routes(), now=NOW)
    assert tick["stage"] == "cost_guard_skip"
    assert "pendingLaunch" not in tick["staticData"]
    assert "claimed" not in tick["staticData"]


def test_pending_instance_not_found_clears_and_alerts(run):
    static = {"pendingInstance": {"id": "ocid1.instance.oc1..missing", "acceptedAt": "2026-11-17T06:00:00Z"}}
    tick = _run_full_tick(run, static, guard_routes(), now=NOW)
    assert tick["notice"]["outcome"] == "terminal_failure"
    assert "pendingInstance" not in tick["staticData"]


def test_read_response_handles_null_headers(run):
    res = respond_raw(run, {}, 429, None, {"code": "TooManyRequests", "message": "Too many"})
    assert first_json(res)["outcome"] == "throttled"
