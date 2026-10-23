"""Tests for the notify and error workflows: the Code nodes run under node on recorded fixtures, plus the wiring.

The Format message node runs on tests/fixtures/notify/*.input.json, the error report's Build failure message node
runs on error-trigger.input.json, and the 429 Wait expression runs on discord-429.response.json.
Discord is never contacted and no network call is made.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / "workflows" / "notify-discord.json"
ERROR_WORKFLOW = ROOT / "workflows" / "error-report.json"
FIXTURES = ROOT / "tests" / "fixtures" / "notify"
EXPECTED = FIXTURES / "expected"
NODE = shutil.which("node")

# Discord colours for each kind (decimal of 0x3498db, 0x2ecc71, 0xe74c3c).
BLUE, GREEN, RED = 3447003, 3066993, 15158332

# Discord limits, as stated in skills/notify-discord/SKILL.md.
LIMIT_CONTENT = 2000
LIMIT_DESCRIPTION = 4096
LIMIT_EMBEDS = 10

# Runs the Code node body the way n8n does: $input.first() gives the item, $env the environment, $('Name') an earlier
# node's output and $getWorkflowStaticData the static data. The context file holds input, env, nodes and staticData.
RUNNER = """
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');
const ctx = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const $ = (name) => ({ first: () => ({ json: ctx.nodes?.[name] ?? null }) });
const staticData = ctx.staticData ?? {};
const run = new Function('$input', '$env', '$', '$getWorkflowStaticData', code);
const out = run({ first: () => ({ json: ctx.input }) }, ctx.env ?? {}, $, () => staticData);
process.stdout.write(JSON.stringify(out[0].json));
"""

# Runs the Count delivery body with $getWorkflowStaticData bound to the context's staticData, then prints that static
# data, so a test passes the metrics object in and reads back what the node wrote.
COUNT_RUNNER = """
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');
const ctx = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const $ = (name) => ({ first: () => ({ json: ctx.nodes?.[name] ?? null }) });
new Function('$input', '$', '$getWorkflowStaticData', code)({ first: () => ({ json: ctx.input }) }, $, () => ctx.staticData);
process.stdout.write(JSON.stringify(ctx.staticData));
"""

# Chains the Check delivery body and then the Count delivery body, the way n8n runs them: a throw in Check delivery stops
# the run before Count delivery. Prints the static data as it stands and whether the run succeeded, so a test can see
# that a failed run left the counters alone.
CHAIN_RUNNER = """
const fs = require('fs');
const check = fs.readFileSync(process.argv[2], 'utf8');
const count = fs.readFileSync(process.argv[3], 'utf8');
const ctx = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const $ = (name) => ({ first: () => ({ json: ctx.nodes?.[name] ?? null }) });
const staticData = ctx.staticData ?? {};
const run = (code, input) => new Function('$input', '$', '$getWorkflowStaticData', code)(
  { first: () => ({ json: input }) }, $, () => staticData);
let ok = true;
let error = null;
try {
  const checked = run(check, ctx.input);
  run(count, checked[0].json);
} catch (e) {
  ok = false;
  error = e.message;
}
process.stdout.write(JSON.stringify({ ok, error, staticData }));
"""

# Evaluates one n8n expression body (the text inside ={{ }}) with $json, $env and $('Name') bound, as n8n does.
# The context file holds json (the item), env and nodes (earlier node outputs by name).
EXPRESSION_RUNNER = """
const fs = require('fs');
const expr = fs.readFileSync(process.argv[2], 'utf8');
const ctx = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const $ = (name) => ({ first: () => ({ json: ctx.nodes?.[name] ?? null }) });
process.stdout.write(JSON.stringify(new Function('$json', '$env', '$', 'return (' + expr + ');')(ctx.json, ctx.env ?? {}, $)));
"""


def load_workflow(path=WORKFLOW):
    return json.loads(path.read_text(encoding="utf-8"))


def node_named(name, path=WORKFLOW):
    return next(node for node in load_workflow(path)["nodes"] if node["name"] == name)


def run_js(tmp_path, runner, code, context):
    """Write the code, the runner and the context to tmp_path and run them under node. Returns the CompletedProcess."""
    code_path = tmp_path / "code.js"
    code_path.write_text(code, encoding="utf-8")
    runner_path = tmp_path / "runner.js"
    runner_path.write_text(runner, encoding="utf-8")
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    return subprocess.run([NODE, str(runner_path), str(code_path), str(context_path)],
                          capture_output=True, text=True, timeout=30, check=False)


def run_code(tmp_path, code, item, env=None, nodes=None):
    """Run one Code node body on one input item, with optional $env values and earlier node outputs."""
    return run_js(tmp_path, RUNNER, code, {"input": item, "env": env or {}, "nodes": nodes or {}})


def run_format(tmp_path, item):
    """Run the Format message node on one input item. Returns the CompletedProcess."""
    return run_code(tmp_path, node_named("Format message")["parameters"]["jsCode"], item)


def format_message(tmp_path, item):
    result = run_format(tmp_path, item)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def build_failure(tmp_path, item):
    """Run the error report's Build failure message node on one Error Trigger payload."""
    code = node_named("Build failure message", ERROR_WORKFLOW)["parameters"]["jsCode"]
    result = run_code(tmp_path, code, item)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def evaluate_expression(tmp_path, expression, item, env=None, nodes=None):
    """Evaluate an n8n expression such as '={{ ... }}' on one item, the way the Wait node reads it."""
    assert expression.startswith("={{") and expression.endswith("}}"), expression
    expr_path = tmp_path / "expr.js"
    expr_path.write_text(expression[3:-2].strip(), encoding="utf-8")
    runner_path = tmp_path / "expr-runner.js"
    runner_path.write_text(EXPRESSION_RUNNER, encoding="utf-8")
    context_path = tmp_path / "expr-context.json"
    context_path.write_text(json.dumps({"json": item, "env": env or {}, "nodes": nodes or {}}), encoding="utf-8")
    result = subprocess.run([NODE, str(runner_path), str(expr_path), str(context_path)],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def render_template(tmp_path, template, item, env=None, nodes=None):
    """Render an n8n string field such as '={{ $env.X }}?thread_id={{ $json.id }}': each {{ }} is evaluated, the rest is text."""
    parts = re.split(r"\{\{(.*?)\}\}", template.removeprefix("="))
    rendered = []
    for index, part in enumerate(parts):
        if index % 2:
            value = evaluate_expression(tmp_path, "={{" + part + "}}", item, env, nodes)
            rendered.append(str(value))
        else:
            rendered.append(part)
    return "".join(rendered)


def fixture(name):
    return json.loads((FIXTURES / f"{name}.input.json").read_text(encoding="utf-8"))


def expected(name):
    return json.loads((EXPECTED / f"{name}.json").read_text(encoding="utf-8"))


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_offer_fixture(tmp_path):
    out = format_message(tmp_path, fixture("offer"))
    assert out == {
        "username": "VM Hunter",
        "content": "3 new free offers\nhttps://free-for-dev.test/new",
        "embeds": [{
            "color": BLUE,
            "description": "- Example Host: 1 GB VPS free for 12 months\n- Demo Cloud: free tier, no card needed\n"
                           "- Sample Labs: free dev instance",
        }],
    }


@needs_node
def test_claim_fixture_shows_attempts_region_and_shape(tmp_path):
    out = format_message(tmp_path, fixture("claim"))
    assert out["content"] == "A1 VM created in eu-frankfurt-1"
    assert out["embeds"] == [{
        "color": GREEN,
        "description": "Free A1 shape claimed after retries.",
        "fields": [
            {"name": "Attempts", "value": "7", "inline": True},
            {"name": "Region", "value": "eu-frankfurt-1", "inline": True},
            {"name": "Shape", "value": "VM.Standard.A1.Flex 4 OCPU / 24 GB", "inline": True},
        ],
    }]


@needs_node
def test_failure_fixture_is_red_with_link(tmp_path):
    out = format_message(tmp_path, fixture("failure"))
    assert out["content"] == "Claim workflow failed\nhttps://n8n.example.test/execution/42"
    assert out["embeds"] == [{"color": RED, "description": "Node 'Launch instance' returned HTTP 500."}]


@needs_node
def test_title_only_claim_has_no_empty_embed(tmp_path):
    out = format_message(tmp_path, {"kind": "claim", "title": "Claim attempt 3 failed: out of capacity"})
    assert out == {"username": "VM Hunter", "content": "Claim attempt 3 failed: out of capacity", "embeds": []}


@needs_node
def test_long_body_is_cut_to_4096_with_ellipsis(tmp_path):
    item = dict(fixture("offer"), body="x" * 5000)
    description = format_message(tmp_path, item)["embeds"][0]["description"]
    assert len(description) == 4096
    assert description.endswith("…")


@needs_node
def test_long_title_is_cut_to_2000_with_ellipsis(tmp_path):
    out = format_message(tmp_path, {"kind": "offer", "title": "t" * 2500})
    assert len(out["content"]) == 2000
    assert out["content"].endswith("…")


@needs_node
def test_unknown_kind_is_rejected(tmp_path):
    result = run_format(tmp_path, {"kind": "toString", "title": "x"})
    assert result.returncode != 0
    assert "unknown kind" in result.stderr


@needs_node
def test_missing_title_is_rejected(tmp_path):
    result = run_format(tmp_path, {"kind": "failure", "body": "no title"})
    assert result.returncode != 0
    assert "title is required" in result.stderr


def test_workflow_identity():
    doc = load_workflow()
    assert doc["id"] == "notifyDiscord01"
    assert doc["active"] is False
    assert doc["settings"] == {"executionOrder": "v1", "errorWorkflow": "errorReport01"}
    assert doc["pinData"] == {}


def test_node_ids_are_unique_uuids():
    ids = [node["id"] for node in load_workflow()["nodes"]]
    assert len(set(ids)) == len(ids)
    for node_id in ids:
        assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", node_id), node_id


POST_URL = "={{ $env.HUNTER_DISCORD_WEBHOOK_URL }}?thread_id={{ encodeURIComponent($json.threadId) }}"
RETRY_URL = ("={{ $env.HUNTER_DISCORD_WEBHOOK_URL }}?thread_id="
             "{{ encodeURIComponent($('Resolve thread').first().json.threadId) }}")


def test_webhook_comes_from_environment_and_never_throws_on_4xx():
    for name in ("Post to Discord", "Retry once"):
        params = node_named(name)["parameters"]
        assert params["url"] == {"Post to Discord": POST_URL, "Retry once": RETRY_URL}[name]
        assert params["options"]["response"]["response"] == {"fullResponse": True, "neverError": True}


@needs_node
def test_thread_id_is_url_encoded_in_the_post_url(tmp_path):
    assert evaluate_expression(tmp_path, "={{ encodeURIComponent($json.threadId) }}",
                               {"threadId": "a b/1?x"}) == "a%20b%2F1%3Fx"


def test_429_branch_waits_retry_after_then_retries_once_without_loop():
    connections = load_workflow()["connections"]
    assert connections["Post to Discord"]["main"] == [[{"node": "Rate limited?", "type": "main", "index": 0}]]
    rate_limited = connections["Rate limited?"]["main"]
    assert rate_limited == [[{"node": "Wait for retry_after", "type": "main", "index": 0}],
                            [{"node": "Check delivery", "type": "main", "index": 0}]]
    assert connections["Wait for retry_after"]["main"] == [[{"node": "Retry once", "type": "main", "index": 0}]]
    # The retry goes only to the delivery check, which goes only to the counter, which has no outgoing connection.
    assert connections["Retry once"]["main"] == [[{"node": "Check delivery", "type": "main", "index": 0}]]
    assert connections["Check delivery"]["main"] == [[{"node": "Count delivery", "type": "main", "index": 0}]]
    assert "Count delivery" not in connections
    condition = node_named("Rate limited?")["parameters"]["conditions"]["conditions"][0]
    assert condition["rightValue"] == 429
    assert "retry_after" in node_named("Wait for retry_after")["parameters"]["amount"]


def test_retry_resends_the_resolved_message_to_the_same_thread():
    assert node_named("Retry once")["parameters"]["jsonBody"] == "={{ $('Resolve thread').first().json.message }}"
    assert node_named("Post to Discord")["parameters"]["jsonBody"] == "={{ $json.message }}"


def test_message_flows_format_then_thread_then_post():
    connections = load_workflow()["connections"]
    assert connections["Format message"]["main"] == [[{"node": "Resolve thread", "type": "main", "index": 0}]]
    assert connections["Resolve thread"]["main"] == [[{"node": "Post to Discord", "type": "main", "index": 0}]]


@needs_node
def test_offer_without_url_matches_expected_discord_body(tmp_path):
    # No url in the input: the content is the title alone, with no link line.
    assert format_message(tmp_path, fixture("offer-no-url")) == expected("offer-no-url")


@needs_node
def test_recorded_fixtures_match_expected_discord_bodies(tmp_path):
    for name in ("offer", "claim", "failure", "offer-no-url"):
        out = format_message(tmp_path, fixture(name))
        assert out["username"] == "VM Hunter", name
        assert set(out) == {"username", "content", "embeds"}, name


@needs_node
def test_long_description_fixture_is_cut_to_4096_and_keeps_its_start(tmp_path):
    body = fixture("long-description")["body"]
    assert len(body) > LIMIT_DESCRIPTION
    description = format_message(tmp_path, fixture("long-description"))["embeds"][0]["description"]
    assert len(description) == LIMIT_DESCRIPTION
    assert description.endswith("…")
    assert description[:-1] == body[:LIMIT_DESCRIPTION - 1]


@needs_node
def test_long_content_fixture_is_cut_to_2000_and_keeps_its_start(tmp_path):
    item = fixture("long-content")
    joined = item["title"] + "\n" + item["url"]
    assert len(joined) > LIMIT_CONTENT
    content = format_message(tmp_path, item)["content"]
    assert len(content) == LIMIT_CONTENT
    assert content.endswith("…")
    assert content[:-1] == joined[:LIMIT_CONTENT - 1]


@needs_node
def test_text_exactly_at_the_limits_is_not_cut(tmp_path):
    title = "t" * LIMIT_CONTENT
    body = "b" * LIMIT_DESCRIPTION
    out = format_message(tmp_path, {"kind": "offer", "title": title, "body": body})
    assert out["content"] == title
    assert out["embeds"][0]["description"] == body
    assert "…" not in out["content"] and "…" not in out["embeds"][0]["description"]


@needs_node
def test_embed_count_stays_within_the_10_limit(tmp_path):
    # The Code node emits at most one embed per message, so the cap of 10 can never be exceeded.
    for name in ("offer", "claim", "failure", "offer-no-url", "long-description", "long-content"):
        embeds = format_message(tmp_path, fixture(name))["embeds"]
        assert len(embeds) <= LIMIT_EMBEDS, name
    code = node_named("Format message")["parameters"]["jsCode"]
    assert f"const LIMIT_EMBEDS = {LIMIT_EMBEDS};" in code
    assert ".slice(0, LIMIT_EMBEDS)" in code


@needs_node
def test_error_trigger_maps_to_failure_kind_with_workflow_node_message_and_link(tmp_path):
    out = build_failure(tmp_path, fixture("error-trigger"))
    assert out == {
        "kind": "failure",
        "title": "Workflow failed: Claim A1 VM",
        "body": "Node: Launch instance\nError: Request failed with status code 500",
        "url": "https://n8n.example.test/workflow/claimA1/executions/42",
    }


@needs_node
def test_error_trigger_flows_through_to_the_expected_discord_body(tmp_path):
    notify_input = build_failure(tmp_path, fixture("error-trigger"))
    assert format_message(tmp_path, notify_input) == expected("error-trigger")


@needs_node
def test_error_trigger_without_node_error_falls_back_to_last_node(tmp_path):
    payload = fixture("error-trigger")
    payload["execution"].pop("error")
    out = build_failure(tmp_path, payload)
    assert out["body"] == "Node: Launch instance\nError: no error message"


@needs_node
def test_error_report_body_and_title_are_cut(tmp_path):
    payload = fixture("error-trigger")
    payload["workflow"]["name"] = "w" * 500
    payload["execution"]["error"]["message"] = "e" * 3000
    out = build_failure(tmp_path, payload)
    assert len(out["title"]) == 200 and out["title"].endswith("…")
    assert len(out["body"]) == 1500 and out["body"].endswith("…")


def test_error_report_wiring():
    doc = load_workflow(ERROR_WORKFLOW)
    assert doc["id"] == "errorReport01"
    assert doc["active"] is False
    trigger = node_named("Error input", ERROR_WORKFLOW)
    assert trigger["type"] == "n8n-nodes-base.errorTrigger"
    assert doc["connections"]["Error input"]["main"] == [[{"node": "Count failure", "type": "main", "index": 0}]]
    assert doc["connections"]["Count failure"]["main"] == [[{"node": "Build failure message", "type": "main", "index": 0}]]
    assert doc["connections"]["Build failure message"]["main"] == [[{"node": "Notify Discord", "type": "main", "index": 0}]]
    notify = node_named("Notify Discord", ERROR_WORKFLOW)["parameters"]["workflowId"]
    assert notify["value"] == load_workflow()["id"] == "notifyDiscord01"


@needs_node
def test_429_fixture_is_routed_to_the_retry_branch(tmp_path):
    envelope = json.loads((FIXTURES / "discord-429.response.json").read_text(encoding="utf-8"))
    status = node_named("Rate limited?")["parameters"]["conditions"]["conditions"][0]["leftValue"]
    assert evaluate_expression(tmp_path, status, envelope) == 429


@needs_node
def test_429_wait_reads_retry_after_from_the_recorded_body(tmp_path):
    wait = node_named("Wait for retry_after")["parameters"]
    assert wait["resume"] == "timeInterval" and wait["unit"] == "seconds"
    envelope = json.loads((FIXTURES / "discord-429.response.json").read_text(encoding="utf-8"))
    assert evaluate_expression(tmp_path, wait["amount"], envelope) == envelope["body"]["retry_after"] == 1.5


@needs_node
def test_429_wait_defaults_to_one_second_without_retry_after(tmp_path):
    wait = node_named("Wait for retry_after")["parameters"]
    assert evaluate_expression(tmp_path, wait["amount"], {"statusCode": 429, "body": {}}) == 1


@needs_node
def test_429_wait_falls_back_to_retry_after_header_in_seconds(tmp_path):
    wait = node_named("Wait for retry_after")["parameters"]
    envelope = {"statusCode": 429, "body": "", "headers": {"retry-after": "2"}}
    assert evaluate_expression(tmp_path, wait["amount"], envelope) == 2


def check_delivery(tmp_path, envelope):
    return run_code(tmp_path, node_named("Check delivery")["parameters"]["jsCode"], envelope)


@needs_node
def test_204_is_success_and_passes_through(tmp_path):
    envelope = {"statusCode": 204, "body": "", "headers": {}}
    result = check_delivery(tmp_path, envelope)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == envelope


@needs_node
@pytest.mark.parametrize("status", [400, 404, 429, 500])
def test_non_2xx_after_post_fails_the_run(tmp_path, status):
    # A 429 here means the single retry was also rate limited; any other error must not count as delivered.
    result = check_delivery(tmp_path, {"statusCode": status, "body": {}, "headers": {}})
    assert result.returncode != 0
    assert f"HTTP {status}" in result.stderr


# Thread routing: which setting each kind reads, with HUNTER_DISCORD_THREAD_ID as the fallback.
THREAD_SETTING = {
    "offer": "HUNTER_DISCORD_THREAD_OFFERS",
    "claim": "HUNTER_DISCORD_THREAD_CLAIMS",
    "failure": "HUNTER_DISCORD_THREAD_FAILURES",
    "summary": "HUNTER_DISCORD_THREAD_SUMMARY",
    "alert": "HUNTER_DISCORD_THREAD_SUMMARY",
}
MESSAGE = {"username": "VM Hunter", "content": "t", "embeds": []}


def resolve_thread(tmp_path, kind, env):
    """Run the Resolve thread node for one kind with the given environment. Returns the CompletedProcess."""
    code = node_named("Resolve thread")["parameters"]["jsCode"]
    return run_code(tmp_path, code, MESSAGE, env=env, nodes={"Notify input": {"kind": kind, "title": "t"}})


@needs_node
@pytest.mark.parametrize("kind", sorted(THREAD_SETTING))
def test_each_kind_uses_its_own_thread_setting(tmp_path, kind):
    result = resolve_thread(tmp_path, kind, {THREAD_SETTING[kind]: "111", "HUNTER_DISCORD_THREAD_ID": "999"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"threadId": "111", "message": MESSAGE}


@needs_node
@pytest.mark.parametrize("kind", sorted(THREAD_SETTING))
def test_unset_setting_falls_back_to_thread_id(tmp_path, kind):
    result = resolve_thread(tmp_path, kind, {"HUNTER_DISCORD_THREAD_ID": "999"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["threadId"] == "999"


@needs_node
@pytest.mark.parametrize("blank", ["", "   ", "\t \n"])
def test_blank_setting_counts_as_unset(tmp_path, blank):
    result = resolve_thread(tmp_path, "claim", {"HUNTER_DISCORD_THREAD_CLAIMS": blank, "HUNTER_DISCORD_THREAD_ID": "999"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["threadId"] == "999"


@needs_node
def test_setting_value_is_trimmed(tmp_path):
    result = resolve_thread(tmp_path, "offer", {"HUNTER_DISCORD_THREAD_OFFERS": " 123 \n"})
    assert json.loads(result.stdout)["threadId"] == "123"


@needs_node
def test_no_thread_id_fails_naming_the_kind_and_the_settings_checked(tmp_path):
    result = resolve_thread(tmp_path, "claim", {"HUNTER_DISCORD_THREAD_CLAIMS": " ", "HUNTER_DISCORD_THREAD_ID": ""})
    assert result.returncode != 0
    assert "kind 'claim'" in result.stderr
    assert "HUNTER_DISCORD_THREAD_CLAIMS, HUNTER_DISCORD_THREAD_ID" in result.stderr


@needs_node
@pytest.mark.parametrize("kind,colour", [("summary", 0x95A5A6), ("alert", 0xF39C12)])
def test_summary_and_alert_kinds_are_accepted_with_their_colour(tmp_path, kind, colour):
    out = format_message(tmp_path, {"kind": kind, "title": "Daily summary", "body": "Claimer · 3 attempts"})
    assert out["embeds"] == [{"color": colour, "description": "Claimer · 3 attempts"}]


# Counters: the Count delivery node runs only after a 2xx, so it counts only successful runs.
def count_delivery(tmp_path, first_status, final_status, static_data):
    """Run Count delivery after a first post that got first_status and a final 2xx. Returns the static data it wrote."""
    code = node_named("Count delivery")["parameters"]["jsCode"]
    context = {"input": {"statusCode": final_status, "body": "", "headers": {}},
               "nodes": {"Post to Discord": {"statusCode": first_status}}, "staticData": static_data}
    result = run_js(tmp_path, COUNT_RUNNER, code, context)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_204_counts_sent_and_keeps_the_rest_of_the_metrics(tmp_path):
    metrics = {"day": "2026-10-23", "counters": {"sent": 4, "rateLimited": 1},
               "lastSuccessAt": {"offers": "2026-10-23T08:00:00Z"}, "alerted": {"claimer-stalled": True}}
    after = count_delivery(tmp_path, 204, 204, {"metrics": metrics})["metrics"]
    assert after == dict(metrics, counters={"sent": 5, "rateLimited": 1})


@needs_node
def test_429_then_204_counts_sent_and_rate_limited(tmp_path):
    after = count_delivery(tmp_path, 429, 204, {"metrics": {"counters": {"sent": 0, "rateLimited": 2}}})
    assert after["metrics"]["counters"] == {"sent": 1, "rateLimited": 3}


@needs_node
def test_counters_start_at_zero_without_metrics(tmp_path):
    assert count_delivery(tmp_path, 204, 204, {})["metrics"]["counters"] == {"sent": 1, "rateLimited": 0}


def test_count_delivery_sits_after_the_delivery_check():
    # Check delivery throws on a non-2xx, so a failed run never reaches the counter (see the monitoring skill).
    assert node_named("Count delivery")["type"] == "n8n-nodes-base.code"
    assert load_workflow()["connections"]["Check delivery"]["main"] == [[{"node": "Count delivery", "type": "main", "index": 0}]]




# Failure counts: the error report's Count failure node counts each failed run per workflow name, in its static data.
def count_failure(tmp_path, payload, static_data):
    """Run Count failure on one Error Trigger payload with the given static data. Returns the static data it wrote."""
    code = node_named("Count failure", ERROR_WORKFLOW)["parameters"]["jsCode"]
    context = {"input": payload, "nodes": {}, "staticData": static_data}
    result = run_js(tmp_path, COUNT_RUNNER, code, context)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_first_failure_of_a_workflow_counts_one_under_its_name(tmp_path):
    payload = json.loads((FIXTURES / "error-trigger.input.json").read_text(encoding="utf-8"))
    after = count_failure(tmp_path, payload, {})["metrics"]
    assert after == {"counters": {"failures": {"Claim A1 VM": 1}}}


@needs_node
def test_failures_add_up_per_name_and_keep_the_rest_of_the_metrics(tmp_path):
    metrics = {"day": "2026-10-23", "counters": {"failures": {"Claim A1 VM": 2, "Watch offers": 1}, "other": 7},
               "alerted": {"failures-claim": True}}
    payload = {"workflow": {"name": "Claim A1 VM"}, "execution": {}}
    after = count_failure(tmp_path, payload, {"metrics": metrics})["metrics"]
    assert after == {"day": "2026-10-23", "counters": {"failures": {"Claim A1 VM": 3, "Watch offers": 1}, "other": 7},
                     "alerted": {"failures-claim": True}}


@needs_node
def test_missing_workflow_name_counts_under_unknown_workflow(tmp_path):
    after = count_failure(tmp_path, {"execution": {}}, {})["metrics"]
    assert after == {"counters": {"failures": {"unknown workflow": 1}}}


def test_count_failure_sits_before_the_message_and_passes_the_payload_on():
    assert node_named("Count failure", ERROR_WORKFLOW)["type"] == "n8n-nodes-base.code"
    connections = load_workflow(ERROR_WORKFLOW)["connections"]
    assert connections["Error input"]["main"] == [[{"node": "Count failure", "type": "main", "index": 0}]]
    assert connections["Count failure"]["main"] == [[{"node": "Build failure message", "type": "main", "index": 0}]]


# Recorded inputs for the cases below. Fake ids only: the thread settings are the fixture's, the webhook is discord.test.
WEBHOOK = "https://discord.test/webhook"
THREAD_ENV = json.loads((FIXTURES / "thread-settings.env.json").read_text(encoding="utf-8"))
THREAD_IDS = {
    "offer": THREAD_ENV["HUNTER_DISCORD_THREAD_OFFERS"],
    "claim": THREAD_ENV["HUNTER_DISCORD_THREAD_CLAIMS"],
    "failure": THREAD_ENV["HUNTER_DISCORD_THREAD_FAILURES"],
    "summary": THREAD_ENV["HUNTER_DISCORD_THREAD_SUMMARY"],
    "alert": THREAD_ENV["HUNTER_DISCORD_THREAD_SUMMARY"],
}
FALLBACK_ID = THREAD_ENV["HUNTER_DISCORD_THREAD_ID"]
METRICS_DAY = "2026-10-23"  # the day the counters belong to, passed in explicitly: no node reads the clock
FAILED_500 = {"statusCode": 500, "body": {"message": "500: Internal Server Error"}, "headers": {}}


def response(name):
    return json.loads((FIXTURES / f"{name}.response.json").read_text(encoding="utf-8"))


def resolved_thread(tmp_path, kind, env):
    result = resolve_thread(tmp_path, kind, env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def deliver(tmp_path, first, final, static_data):
    """Run Check delivery then Count delivery, as the notifier does after a post that got first and ended at final."""
    code_dir = tmp_path / "chain"
    code_dir.mkdir(exist_ok=True)
    check_path = code_dir / "check.js"
    check_path.write_text(node_named("Check delivery")["parameters"]["jsCode"], encoding="utf-8")
    count_path = code_dir / "count.js"
    count_path.write_text(node_named("Count delivery")["parameters"]["jsCode"], encoding="utf-8")
    runner_path = code_dir / "chain-runner.js"
    runner_path.write_text(CHAIN_RUNNER, encoding="utf-8")
    context_path = code_dir / "chain-context.json"
    context_path.write_text(json.dumps({"input": final, "nodes": {"Post to Discord": first}, "staticData": static_data}),
                            encoding="utf-8")
    result = subprocess.run([NODE, str(runner_path), str(check_path), str(count_path), str(context_path)],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def count_failures(tmp_path, payloads):
    """Run Count failure once per payload, feeding each run's static data into the next, as n8n saves it between runs."""
    static_data = {}
    for payload in payloads:
        static_data = count_failure(tmp_path, payload, static_data)
    return static_data


# (a) Thread selection, per kind: a blank or unset kind setting falls back, and a missing thread id fails clearly.
@needs_node
@pytest.mark.parametrize("kind", sorted(THREAD_SETTING))
def test_fixture_env_routes_each_kind_to_its_own_setting(tmp_path, kind):
    assert resolved_thread(tmp_path, kind, THREAD_ENV)["threadId"] == THREAD_IDS[kind]


@needs_node
@pytest.mark.parametrize("kind", sorted(THREAD_SETTING))
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_kind_setting_falls_back_per_kind(tmp_path, kind, blank):
    env = dict(THREAD_ENV, **{THREAD_SETTING[kind]: blank})
    assert resolved_thread(tmp_path, kind, env)["threadId"] == FALLBACK_ID


@needs_node
@pytest.mark.parametrize("kind", sorted(THREAD_SETTING))
def test_unset_kind_setting_falls_back_per_kind(tmp_path, kind):
    env = {key: value for key, value in THREAD_ENV.items() if key != THREAD_SETTING[kind]}
    assert resolved_thread(tmp_path, kind, env)["threadId"] == FALLBACK_ID


@needs_node
@pytest.mark.parametrize("kind", sorted(THREAD_SETTING))
def test_no_thread_id_names_the_kind_and_both_settings_per_kind(tmp_path, kind):
    result = resolve_thread(tmp_path, kind, {THREAD_SETTING[kind]: "", "HUNTER_DISCORD_THREAD_ID": "  "})
    assert result.returncode != 0
    assert f"no Discord thread for kind '{kind}'" in result.stderr
    assert f"(checked {THREAD_SETTING[kind]}, HUNTER_DISCORD_THREAD_ID)" in result.stderr


@needs_node
def test_summary_and_alert_both_go_to_the_summary_thread_whatever_the_other_settings_say(tmp_path):
    for kind in ("summary", "alert"):
        assert resolved_thread(tmp_path, kind, THREAD_ENV)["threadId"] == THREAD_IDS["summary"]
    # Only the summary setting and the fallback are set: neither kind uses the fallback.
    only_summary = {"HUNTER_DISCORD_THREAD_SUMMARY": "555", "HUNTER_DISCORD_THREAD_ID": "999"}
    for kind in ("summary", "alert"):
        assert resolved_thread(tmp_path, kind, only_summary)["threadId"] == "555"


# (c) Summary and alert inputs, recorded: the expected Discord bodies, and the thread each one resolves to.
@needs_node
@pytest.mark.parametrize("name", ["summary", "alert"])
def test_summary_and_alert_fixtures_match_expected_payloads_and_route_to_summary(tmp_path, name):
    notify_input = fixture(name)
    message = format_message(tmp_path, notify_input)
    assert message == expected(name)
    code = node_named("Resolve thread")["parameters"]["jsCode"]
    result = run_code(tmp_path, code, message, env=THREAD_ENV, nodes={"Notify input": notify_input})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"threadId": THREAD_IDS["summary"], "message": message}


# (b) The URL carries ?thread_id= on the first POST and on the 429 retry, built from the resolved thread.
@needs_node
@pytest.mark.parametrize("kind", sorted(THREAD_SETTING))
def test_first_post_url_carries_the_thread_id_per_kind(tmp_path, kind):
    resolved = resolved_thread(tmp_path, kind, THREAD_ENV)
    url = render_template(tmp_path, POST_URL, resolved, env={"HUNTER_DISCORD_WEBHOOK_URL": WEBHOOK})
    assert url == f"{WEBHOOK}?thread_id={THREAD_IDS[kind]}"


@needs_node
@pytest.mark.parametrize("kind", sorted(THREAD_SETTING))
def test_429_retry_url_carries_the_same_thread_id_per_kind(tmp_path, kind):
    resolved = resolved_thread(tmp_path, kind, THREAD_ENV)
    # The retry reads the thread from Resolve thread, not from the 429 response it follows, which has no thread id.
    url = render_template(tmp_path, RETRY_URL, response("discord-429"), env={"HUNTER_DISCORD_WEBHOOK_URL": WEBHOOK},
                          nodes={"Resolve thread": resolved})
    assert url == f"{WEBHOOK}?thread_id={THREAD_IDS[kind]}"


@needs_node
def test_retry_url_uses_the_fallback_thread_when_the_kind_setting_is_blank(tmp_path):
    env = dict(THREAD_ENV, HUNTER_DISCORD_THREAD_CLAIMS="")
    resolved = resolved_thread(tmp_path, "claim", env)
    url = render_template(tmp_path, RETRY_URL, response("discord-429"), env={"HUNTER_DISCORD_WEBHOOK_URL": WEBHOOK},
                          nodes={"Resolve thread": resolved})
    assert url == f"{WEBHOOK}?thread_id={FALLBACK_ID}"


# (e) Counters: sent and rateLimited move only on a successful run; a failed run leaves them as they were.
@needs_node
def test_204_run_counts_sent_only(tmp_path):
    metrics = {"day": METRICS_DAY, "counters": {"sent": 4, "rateLimited": 1}}
    result = deliver(tmp_path, response("discord-204"), response("discord-204"), {"metrics": metrics})
    assert result["ok"] is True
    assert result["staticData"]["metrics"] == {"day": METRICS_DAY, "counters": {"sent": 5, "rateLimited": 1}}


@needs_node
def test_429_then_204_run_counts_sent_and_rate_limited(tmp_path):
    metrics = {"day": METRICS_DAY, "counters": {"sent": 0, "rateLimited": 2}}
    result = deliver(tmp_path, response("discord-429"), response("discord-204"), {"metrics": metrics})
    assert result["ok"] is True
    assert result["staticData"]["metrics"] == {"day": METRICS_DAY, "counters": {"sent": 1, "rateLimited": 3}}


@needs_node
def test_repeated_runs_add_up_across_204_and_429_then_204(tmp_path):
    static_data = {}
    for first in (response("discord-204"), response("discord-429"), response("discord-204")):
        static_data = deliver(tmp_path, first, response("discord-204"), static_data)["staticData"]
    assert static_data["metrics"]["counters"] == {"sent": 3, "rateLimited": 1}


@needs_node
@pytest.mark.parametrize("first,final", [
    (response("discord-429"), FAILED_500),
    (response("discord-429"), response("discord-429")),
    (FAILED_500, FAILED_500),
])
def test_failed_run_leaves_the_counters_unchanged(tmp_path, first, final):
    metrics = {"day": METRICS_DAY, "counters": {"sent": 4, "rateLimited": 1}}
    before = {"metrics": metrics}
    result = deliver(tmp_path, first, final, before)
    assert result["ok"] is False
    assert f"HTTP {final['statusCode']}" in result["error"]
    # Count delivery never ran: with no metrics it would have written counters, so an empty start stays empty.
    assert deliver(tmp_path, first, final, {})["staticData"] == {}
    assert result["staticData"] == before


# (f) Error report failures: counted per workflow name over repeated failures, in the error workflow's static data.
@needs_node
def test_two_workflow_names_and_repeated_failures_count_separately(tmp_path):
    claim = fixture("error-trigger")
    watch = json.loads((FIXTURES / "error-trigger-watch-offers.input.json").read_text(encoding="utf-8"))
    static_data = count_failures(tmp_path, [claim, watch, claim, claim, watch, claim])
    assert static_data["metrics"]["counters"]["failures"] == {"Claim A1 VM": 4, "Watch offers": 2}


@needs_node
def test_repeated_failures_of_one_workflow_keep_counting(tmp_path):
    watch = json.loads((FIXTURES / "error-trigger-watch-offers.input.json").read_text(encoding="utf-8"))
    static_data = count_failures(tmp_path, [watch] * 5)
    assert static_data["metrics"]["counters"]["failures"] == {"Watch offers": 5}
