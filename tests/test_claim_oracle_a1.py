"""The claim-oracle-a1 workflow: signer, cost guard, response handling, waits and counters.

The jsCode of each Code node is taken from workflows/claim-oracle-a1.json and run under node with
recorded fake-OCID fixtures (tests/fixtures/oracle/). The HTTP helper only answers recorded routes:
any other request fails the run, so no live call can happen.
"""
import base64
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "oracle"
WORKFLOW = json.loads((ROOT / "workflows" / "claim-oracle-a1.json").read_text(encoding="utf-8"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or shutil.which("openssl") is None,
    reason="needs node and openssl on PATH",
)

BASE = "https://iaas.oci.test"
HOST = "iaas.oci.test"
COMPARTMENT = "ocid1.compartment.oc1..example"
AD = "EXAMPLE-AD-1"
NEXT_PAGE = "page-2-example"
NOW = 1_800_000_000_000  # fixed clock in milliseconds, so waits are exact
ENV = {
    "HUNTER_OCI_API_BASE": BASE,
    "HUNTER_OCI_TENANCY_OCID": "ocid1.tenancy.oc1..example",
    "HUNTER_OCI_USER_OCID": "ocid1.user.oc1..example",
    "HUNTER_OCI_KEY_FINGERPRINT": "example-fingerprint",
    "HUNTER_OCI_COMPARTMENT_OCID": COMPARTMENT,
    "HUNTER_OCI_AVAILABILITY_DOMAIN": AD,
    "HUNTER_OCI_SUBNET_OCID": "ocid1.subnet.oc1..example",
    "HUNTER_OCI_IMAGE_OCID": "ocid1.image.oc1..example",
    "HUNTER_OCI_SSH_PUBLIC_KEY": "ssh-ed25519 AAAAEXAMPLE vm-hunter-test",
    "GENERIC_TIMEZONE": "",
}
REQUEST = {"shape": "VM.Standard.A1.Flex", "ocpus": 4, "memoryInGBs": 24, "bootVolumeSizeInGBs": 50, "displayName": "vm-hunter-a1"}
LIST_INSTANCES = f"GET /20160918/instances?compartmentId={COMPARTMENT}"
LIST_BOOT = f"GET /20160918/bootVolumes?compartmentId={COMPARTMENT}&availabilityDomain={AD}"
LIST_BLOCK = f"GET /20160918/volumes?compartmentId={COMPARTMENT}"
LAUNCH = "POST /20160918/instances"

# Runs one Code node body (or a jitter expression) under node. Standard library only; no network.
RUNNER = r'''
const fs = require('fs');
const scenario = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
Date.now = () => scenario.now;

async function main() {
  if (scenario.expression) {
    const results = scenario.expression.randoms.map((value) => {
      const fakeMath = Object.create(Math);
      fakeMath.random = () => value;
      return new Function('Math', `return ${scenario.expression.code};`)(fakeMath);
    });
    return { result: results };
  }
  const requests = [];
  const helpers = {
    httpRequest: async (options) => {
      const target = options.url.slice(scenario.env.HUNTER_OCI_API_BASE.length);
      requests.push({ method: options.method, target, headers: options.headers ?? {}, body: options.body ?? null });
      const route = scenario.routes[`${options.method} ${target}`];
      if (!route) throw new Error(`no recorded route for ${options.method} ${target}`);
      return { statusCode: route.status, headers: route.headers, body: route.body };
    },
  };
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const body = new AsyncFunction('$input', '$env', '$getWorkflowStaticData', 'require', scenario.code);
  const first = { json: scenario.input };
  const result = await body.call({ helpers }, { first: () => first }, scenario.env, () => scenario.staticData, require);
  return { result, staticData: scenario.staticData, requests };
}

main().then(
  (out) => process.stdout.write(JSON.stringify(out)),
  (error) => { process.stderr.write(String((error && error.stack) || error)); process.exit(1); },
);
'''


def node_code(name):
    """The jsCode of one Code node in the workflow."""
    for node in WORKFLOW["nodes"]:
        if node["name"] == name:
            return node["parameters"]["jsCode"]
    raise KeyError(name)


def node_parameter(name, key):
    for node in WORKFLOW["nodes"]:
        if node["name"] == name:
            return node["parameters"][key]
    raise KeyError(name)


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def page(items, next_page=""):
    """One list response: the array, plus opc-next-page when another page follows."""
    return {"status": 200, "headers": {"opc-next-page": next_page} if next_page else {}, "body": items}


def a1(ocpus, memory, state="RUNNING", shape="VM.Standard.A1.Flex"):
    item = dict(fixture("list-instances.json")["body"][0])
    item.update(shape=shape, lifecycleState=state, shapeConfig={"ocpus": float(ocpus), "memoryInGBs": float(memory)})
    return item


def volume(size, state="AVAILABLE"):
    item = dict(fixture("list-boot-volumes.json")["body"][0])
    item.update(sizeInGBs=size, lifecycleState=state)
    return item


def guard_routes(instances=None, boot=None, block=None):
    return {
        LIST_INSTANCES: instances or page([]),
        LIST_BOOT: boot or page([]),
        LIST_BLOCK: block or page([]),
    }


def first_json(res):
    return res["result"][0]["json"]


def openssl_digest_b64(tmp_path, data):
    message = tmp_path / "body.bin"
    message.write_bytes(data)
    raw = subprocess.run(["openssl", "dgst", "-sha256", "-binary", str(message)], capture_output=True, check=True).stdout
    return base64.b64encode(raw).decode()


def openssl_signature(tmp_path, key, signing_string):
    """RSA-SHA256 over the signing string, as openssl makes it: the reference the signer must match."""
    message = tmp_path / "signing-string.txt"
    message.write_bytes(signing_string.encode())
    raw = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key), str(message)], capture_output=True, check=True).stdout
    return base64.b64encode(raw).decode()


def signature_of(authorization):
    return re.search(r'signature="([^"]+)"', authorization).group(1)


@pytest.fixture(scope="session")
def key(tmp_path_factory):
    """A throwaway RSA key, generated for the tests only."""
    path = tmp_path_factory.mktemp("throwaway-rsa") / "key.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(path), "2048"], check=True, capture_output=True)
    return path


@pytest.fixture
def run(tmp_path, key):
    def _run(code, *, input=None, static=None, routes=None, env=None, now=NOW):
        scenario = {
            "code": code,
            "env": {**ENV, "HUNTER_OCI_PRIVATE_KEY_PEM": key.read_text(encoding="utf-8"), **(env or {})},
            "input": input or {},
            "staticData": static if static is not None else {},
            "routes": routes or {},
            "now": now,
        }
        return run_node(tmp_path, scenario)

    return _run


def run_node(tmp_path, scenario):
    runner = tmp_path / "runner.cjs"
    runner.write_text(RUNNER, encoding="utf-8")
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(json.dumps(scenario), encoding="utf-8")
    done = subprocess.run(["node", str(runner), str(scenario_file)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def respond(run, static, name):
    """Run the response reader on one recorded launch response."""
    recorded = fixture(name)
    return run(node_code("Read response"), input={"statusCode": recorded["status"], "headers": recorded["headers"], "body": recorded["body"]}, static=static)


def respond_raw(run, static, status, headers, body):
    return run(node_code("Read response"), input={"statusCode": status, "headers": headers, "body": body}, static=static)


# ---- signer ---------------------------------------------------------------------------------

def test_launch_signature_matches_openssl(run, tmp_path, key):
    res = run(node_code("Launch instance"), input={"request": REQUEST}, routes={LAUNCH: fixture("launch-200-instance.json")})

    [sent] = res["requests"]
    headers, body = sent["headers"], sent["body"]
    assert sent["method"] == "POST" and sent["target"] == "/20160918/instances"
    assert headers["content-length"] == str(len(body.encode()))
    assert headers["x-content-sha256"] == openssl_digest_b64(tmp_path, body.encode())

    signing = "\n".join([
        "(request-target): post /20160918/instances",
        f"date: {headers['date']}",
        f"host: {HOST}",
        f"x-content-sha256: {headers['x-content-sha256']}",
        f"content-type: {headers['content-type']}",
        f"content-length: {headers['content-length']}",
    ])
    authorization = headers["authorization"]
    assert authorization.startswith(
        'Signature version="1",keyId="ocid1.tenancy.oc1..example/ocid1.user.oc1..example/example-fingerprint",'
        'algorithm="rsa-sha256",headers="(request-target) date host x-content-sha256 content-type content-length",signature="'
    )
    assert signature_of(authorization) == openssl_signature(tmp_path, key, signing)

    sent_body = json.loads(body)
    assert sent_body["shape"] == "VM.Standard.A1.Flex"
    assert sent_body["shapeConfig"] == {"ocpus": 4, "memoryInGBs": 24}
    assert sent_body["sourceDetails"] == {"sourceType": "image", "imageId": "ocid1.image.oc1..example", "bootVolumeSizeInGBs": 50}
    assert sent_body["createVnicDetails"] == {"subnetId": "ocid1.subnet.oc1..example", "assignPublicIp": True}
    assert sent_body["metadata"] == {"ssh_authorized_keys": "ssh-ed25519 AAAAEXAMPLE vm-hunter-test"}
    assert first_json(res)["statusCode"] == 200


# ---- cost guard -----------------------------------------------------------------------------

def test_guard_allows_an_empty_tenancy(run):
    res = run(node_code("Cost guard"), routes=guard_routes())
    assert first_json(res) == {"kind": "", "title": "", "body": "", "url": "", "request": REQUEST}
    assert res["staticData"] == {}


def test_guard_refuses_ocpus_over_the_limit(run):
    routes = guard_routes(instances=fixture("list-instances.json"), boot=fixture("list-boot-volumes.json"), block=fixture("list-block-volumes.json"))
    item = first_json(run(node_code("Cost guard"), routes=routes))
    assert item["kind"] == "failure" and item["title"] == "Cost guard refused the A1 launch"
    assert "OCPUs would be 6, the limit is 4" in item["body"]
    assert "Existing: 2 OCPU, 12 GB memory, 97 GB storage." in item["body"]
    assert item["body"].endswith("The claimer has stopped.")


def test_guard_refusal_stops_the_claimer_and_counts_it(run):
    res = run(node_code("Cost guard"), routes=guard_routes(instances=fixture("list-instances.json")))
    assert first_json(res)["kind"] == "failure"
    assert res["staticData"]["stopped"] is True
    assert res["staticData"]["metrics"]["counters"]["costGuardRefusals"] == 1


def test_guard_counts_only_live_a1_instances(run):
    instances = page([
        a1(4, 24, state="TERMINATED"),
        a1(1, 1, shape="VM.Standard.E2.1.Micro"),
    ])
    item = first_json(run(node_code("Cost guard"), routes=guard_routes(instances=instances)))
    assert item["kind"] == ""


def test_guard_counts_a_terminating_instance_that_is_not_yet_terminated(run):
    # Only TERMINATED is excluded from instances; a TERMINATING instance still holds its OCPUs.
    item = first_json(run(node_code("Cost guard"), routes=guard_routes(instances=page([a1(1, 6, state="TERMINATING")]))))
    assert item["kind"] == "failure" and "OCPUs would be 5" in item["body"]


def test_guard_refuses_memory_over_the_limit(run):
    # Memory is checked on its own. Real A1 instances have at least one OCPU, so this uses a zero-OCPU
    # instance: the OCPU check then passes and only the memory line is reported.
    item = first_json(run(node_code("Cost guard"), routes=guard_routes(instances=page([a1(0, 1)]))))
    assert item["kind"] == "failure"
    assert "memory would be 25 GB, the limit is 24 GB" in item["body"]
    assert "OCPUs" not in item["body"]


def test_guard_storage_limit_is_inclusive_at_200_gb(run):
    allowed = first_json(run(node_code("Cost guard"), routes=guard_routes(boot=page([volume(100)]), block=page([volume(50)]))))
    assert allowed["kind"] == ""  # 100 + 50 + 50 requested = 200


def test_guard_refuses_storage_over_200_gb(run):
    item = first_json(run(node_code("Cost guard"), routes=guard_routes(boot=page([volume(100)]), block=page([volume(60)]))))
    assert item["kind"] == "failure"
    assert "storage would be 210 GB, the limit is 200 GB" in item["body"]


def test_guard_ignores_terminated_and_terminating_volumes(run):
    volumes = page([volume(500, state="TERMINATED")])
    blocks = page([volume(400, state="TERMINATING")])
    assert first_json(run(node_code("Cost guard"), routes=guard_routes(boot=volumes, block=blocks)))["kind"] == ""


def test_guard_counts_every_instance_page(run):
    routes = guard_routes()
    routes[LIST_INSTANCES] = page([a1(1, 6)], next_page=NEXT_PAGE)
    routes[f"{LIST_INSTANCES}&page={NEXT_PAGE}"] = page([a1(2, 12)])
    res = run(node_code("Cost guard"), routes=routes)
    item = first_json(res)
    assert "OCPUs would be 7, the limit is 4" in item["body"]
    assert "Existing: 3 OCPU, 18 GB memory, 0 GB storage." in item["body"]
    assert f"/20160918/instances?compartmentId={COMPARTMENT}&page={NEXT_PAGE}" in [r["target"] for r in res["requests"]]


def test_guard_counts_every_volume_page(run):
    routes = guard_routes()
    routes[LIST_BOOT] = page([volume(100)], next_page=NEXT_PAGE)
    routes[f"{LIST_BOOT}&page={NEXT_PAGE}"] = page([volume(60)])
    item = first_json(run(node_code("Cost guard"), routes=routes))
    assert "storage would be 210 GB" in item["body"]


@pytest.mark.parametrize("route, fixture_name, message", [
    (LIST_INSTANCES, "launch-404-not-found.json", "/20160918/instances returned HTTP 404"),
    (LIST_BOOT, "launch-500-out-of-capacity.json", "/20160918/bootVolumes returned HTTP 500"),
    (LIST_BLOCK, "launch-404-not-found.json", "/20160918/volumes returned HTTP 404"),
])
def test_guard_refuses_when_any_list_fails(run, route, fixture_name, message):
    routes = guard_routes()
    recorded = fixture(fixture_name)
    routes[route] = {"status": recorded["status"], "headers": recorded["headers"], "body": recorded["body"]}
    res = run(node_code("Cost guard"), routes=routes)
    item = first_json(res)
    assert item["kind"] == "failure"
    assert f"could not list existing resources: {message}" in item["body"]
    assert res["staticData"]["stopped"] is True


def test_guard_list_requests_are_signed_like_openssl(run, tmp_path, key):
    res = run(node_code("Cost guard"), routes=guard_routes())
    get = res["requests"][0]
    assert get["method"] == "GET" and get["target"] == f"/20160918/instances?compartmentId={COMPARTMENT}"
    signing = "\n".join([
        f"(request-target): get {get['target']}",
        f"date: {get['headers']['date']}",
        f"host: {HOST}",
    ])
    assert get["headers"]["authorization"].startswith('Signature version="1",keyId="ocid1.tenancy.oc1..example/')
    assert signature_of(get["headers"]["authorization"]) == openssl_signature(tmp_path, key, signing)


# ---- response handling: one test per row of the provider-oracle table -------------------------

def test_claimed_row_records_the_claim(run):
    res = respond(run, {}, "launch-200-instance.json")
    out = first_json(res)
    assert out["outcome"] == "claimed"
    assert out["kind"] == "claim"
    assert out["title"] == "A1 VM created: vm-hunter-a1"
    assert "Instance ocid1.instance.oc1..example" in out["body"]
    assert res["staticData"]["claimed"]["id"] == "ocid1.instance.oc1..example"
    assert res["staticData"]["metrics"]["counters"] == {"attempts": 1, "success": 1}


def test_out_of_capacity_row_waits_and_keeps_trying(run):
    res = respond(run, {}, "launch-500-out-of-capacity.json")
    out = first_json(res)
    assert out == {"outcome": "capacity", "kind": "", "title": "", "body": "", "url": ""}
    assert "claimed" not in res["staticData"] and "stopped" not in res["staticData"]
    assert res["staticData"]["metrics"]["counters"] == {"attempts": 1, "outOfCapacity": 1}


def test_throttled_row_doubles_the_wait(run):
    res = respond(run, {}, "launch-429-throttled.json")
    out = first_json(res)
    assert out["outcome"] == "throttled" and out["kind"] == ""
    assert res["staticData"]["metrics"]["counters"] == {"attempts": 1, "rateLimited": 1}
    assert res["staticData"]["metrics"]["consecutive429"] == 1


@pytest.mark.parametrize("name, status, code", [
    ("launch-401-not-authenticated.json", 401, "NotAuthenticated"),
    ("launch-400-invalid-parameter.json", 400, "InvalidParameter"),
    ("launch-404-not-found.json", 404, "NotAuthorizedOrNotFound"),
])
def test_configuration_rows_stop_the_claimer(run, name, status, code):
    res = respond(run, {}, name)
    out = first_json(res)
    assert out["outcome"] == "config"
    assert out["kind"] == "failure"
    assert out["title"] == f"A1 claim stopped: HTTP {status}"
    assert f"{code}:" in out["body"]
    assert f"Request id: EXAMPLE-REQUEST-ID-{status}" in out["body"]
    assert res["staticData"]["stopped"] is True
    assert res["staticData"]["metrics"]["counters"] == {"attempts": 1, "configErrors": 1}


def test_other_status_counts_as_an_error_and_does_not_stop(run):
    res = respond_raw(run, {}, 503, {"opc-request-id": "EXAMPLE-REQUEST-ID-503"}, {"code": "ServiceUnavailable", "message": "Try again."})
    assert first_json(res)["outcome"] == "error"
    assert "stopped" not in res["staticData"]
    assert res["staticData"]["metrics"]["counters"] == {"attempts": 1, "errors": 1}


# ---- waits and jitter -------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "launch-200-instance.json",
    "launch-500-out-of-capacity.json",
    "launch-401-not-authenticated.json",
])
def test_every_attempt_waits_at_least_60_seconds(run, name):
    res = respond(run, {}, name)
    assert res["staticData"]["nextAttemptAt"] - NOW == 60_000


def test_jitter_is_between_zero_and_thirty_seconds(tmp_path):
    amount = node_parameter("Jitter", "amount")  # "={{ Math.floor(Math.random() * 31) }}"
    expression = re.search(r"\{\{\s*(.+?)\s*\}\}", amount).group(1)
    assert node_parameter("Jitter", "unit") == "seconds"
    res = run_node(tmp_path, {"expression": {"code": expression, "randoms": [0, 0.5, 0.999999]}})
    assert res["result"] == [0, 15, 30]


def test_429_doubles_from_60_seconds_and_caps_at_15_minutes(run):
    static = {}
    waits = []
    for _ in range(6):
        res = respond_raw(run, static, 429, {}, {"code": "TooManyRequests", "message": "Too many requests for the user"})
        static = res["staticData"]
        waits.append(static["nextAttemptAt"] - NOW)
    assert waits == [60_000, 120_000, 240_000, 480_000, 900_000, 900_000]


def test_429_reaching_the_cap_alerts_once_per_day(run):
    static = {"metrics": {"consecutive429": 4, "counters": {}, "alerted": {}}}
    first = respond_raw(run, static, 429, {}, {"code": "TooManyRequests", "message": "Too many requests for the user"})
    assert first_json(first)["kind"] == "alert"
    assert first["staticData"]["metrics"]["alerted"]["capReached"]
    again = respond_raw(run, first["staticData"], 429, {}, {"code": "TooManyRequests", "message": "Too many requests for the user"})
    assert first_json(again)["kind"] == ""


def test_retry_after_is_honoured_within_the_cap(run):
    short = respond_raw(run, {}, 429, {"retry-after": "300"}, {"code": "TooManyRequests", "message": "Too many requests for the user"})
    assert short["staticData"]["nextAttemptAt"] - NOW == 300_000
    long = respond_raw(run, {}, 429, {"retry-after": "1200"}, {"code": "TooManyRequests", "message": "Too many requests for the user"})
    assert long["staticData"]["nextAttemptAt"] - NOW == 900_000


def test_a_non_429_response_resets_the_429_streak(run):
    static = respond_raw(run, {}, 429, {}, {"code": "TooManyRequests", "message": "Too many requests for the user"})["staticData"]
    static = respond(run, static, "launch-500-out-of-capacity.json")["staticData"]
    res = respond_raw(run, static, 429, {}, {"code": "TooManyRequests", "message": "Too many requests for the user"})
    assert res["staticData"]["nextAttemptAt"] - NOW == 60_000


# ---- stop after success, due checks and counters ---------------------------------------------

def test_due_only_once_the_wait_has_passed(run):
    static = {"nextAttemptAt": NOW + 60_000}
    assert run(node_code("Due for attempt?"), static=static, now=NOW + 59_999)["result"] == []
    assert run(node_code("Due for attempt?"), static=static, now=NOW + 60_000)["result"] == [{"json": {"due": True}}]


def test_an_attempt_holds_the_claimer_while_it_runs(run):
    res = run(node_code("Due for attempt?"), static={}, now=NOW)
    assert res["staticData"]["nextAttemptAt"] - NOW == 15 * 60_000
    assert run(node_code("Due for attempt?"), static=res["staticData"], now=NOW + 60_000)["result"] == []


def test_a_claim_stops_the_claimer(run):
    static = respond(run, {}, "launch-200-instance.json")["staticData"]
    later = NOW + 24 * 60 * 60_000
    assert run(node_code("Due for attempt?"), static=static, now=later)["result"] == []


def test_a_configuration_error_stops_the_claimer(run):
    static = run(node_code("Due for attempt?"), static={})["staticData"]
    static = respond(run, static, "launch-401-not-authenticated.json")["staticData"]
    assert run(node_code("Due for attempt?"), static=static, now=NOW + 24 * 60 * 60_000)["result"] == []


def test_changed_settings_start_a_stopped_claimer_again(run):
    static = run(node_code("Due for attempt?"), static={})["staticData"]
    static = respond(run, static, "launch-401-not-authenticated.json")["staticData"]
    later = NOW + 24 * 60 * 60_000
    res = run(node_code("Due for attempt?"), static=static, now=later, env={"HUNTER_OCI_A1_OCPUS": "2"})
    assert res["result"] == [{"json": {"due": True}}]
    assert "stopped" not in res["staticData"]


def test_a_stop_from_before_settings_were_tracked_is_lifted_once(run):
    res = run(node_code("Due for attempt?"), static={"stopped": True})
    assert res["result"] == [{"json": {"due": True}}]


def test_a_claim_is_never_lifted_by_changed_settings(run):
    static = respond(run, {"config": "old"}, "launch-200-instance.json")["staticData"]
    res = run(node_code("Due for attempt?"), static=static, now=NOW + 60_000, env={"HUNTER_OCI_A1_OCPUS": "2"})
    assert res["result"] == []


def test_the_requested_size_comes_from_the_environment(run):
    res = run(node_code("Cost guard"), routes=guard_routes(), env={"HUNTER_OCI_A1_OCPUS": "2", "HUNTER_OCI_A1_MEMORY_GB": "12"})
    request = first_json(res)["request"]
    assert (request["ocpus"], request["memoryInGBs"]) == (2, 12)


def test_out_of_capacity_keeps_the_claimer_due(run):
    static = respond(run, {}, "launch-500-out-of-capacity.json")["staticData"]
    assert run(node_code("Due for attempt?"), static=static, now=NOW + 60_000)["result"] == [{"json": {"due": True}}]


def test_counters_count_each_outcome_across_attempts(run):
    static = {}
    for name in ["launch-500-out-of-capacity.json", "launch-429-throttled.json", "launch-500-out-of-capacity.json"]:
        static = respond(run, static, name)["staticData"]
    static = respond_raw(run, static, 503, {}, {"code": "ServiceUnavailable", "message": "Try again."})["staticData"]
    static = respond(run, static, "launch-200-instance.json")["staticData"]
    assert static["metrics"]["counters"] == {
        "attempts": 5, "outOfCapacity": 2, "rateLimited": 1, "errors": 1, "success": 1,
    }


def test_shape_summary_reports_and_resets_the_counters(run):
    static = {
        "claimed": {"id": "ocid1.instance.oc1..example"},
        "metrics": {
            "day": "2026-10-23",
            "counters": {"attempts": 5, "outOfCapacity": 2, "rateLimited": 1, "errors": 1, "success": 1},
            "alerted": {"capReached": "2026-10-23"},
        },
    }
    res = run(node_code("Shape summary"), static=static)
    out = first_json(res)
    assert out["kind"] == "summary"
    assert out["title"] == "Claimer summary (2026-10-23)"
    assert out["body"] == "Claimer · 5 attempts · 2 out of capacity · 1 rate limited · 1 errors · 0 cost guard refusals · claimed"
    metrics = res["staticData"]["metrics"]
    assert metrics["counters"] == {}
    assert metrics["alerted"] == {"capReached": "2026-10-23"}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", metrics["day"])


def test_code_nodes_do_not_use_the_url_global():
    # The n8n Code node sandbox (1.123.87) has no URL global; `new URL(...)` fails there with
    # "URL is not defined" although it works under plain node in these tests.
    for path in sorted((ROOT / "workflows").glob("*.json")):
        workflow = json.loads(path.read_text(encoding="utf-8"))
        for node in workflow["nodes"]:
            assert "new URL(" not in node.get("parameters", {}).get("jsCode", ""), (path.name, node["name"])
