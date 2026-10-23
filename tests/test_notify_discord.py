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

# Runs the Code node body the way n8n does: $input.first() gives the item, the body returns the output items.
RUNNER = """
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');
const input = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const run = new Function('$input', code);
const out = run({ first: () => ({ json: input }) });
process.stdout.write(JSON.stringify(out[0].json));
"""

# Evaluates one n8n expression body (the text inside ={{ }}) with $json bound to the given item.
EXPRESSION_RUNNER = """
const fs = require('fs');
const expr = fs.readFileSync(process.argv[2], 'utf8');
const json = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
process.stdout.write(JSON.stringify(new Function('$json', 'return (' + expr + ');')(json)));
"""


def load_workflow(path=WORKFLOW):
    return json.loads(path.read_text(encoding="utf-8"))


def node_named(name, path=WORKFLOW):
    return next(node for node in load_workflow(path)["nodes"] if node["name"] == name)


def run_code(tmp_path, code, item):
    """Run one Code node body on one input item. Returns the CompletedProcess."""
    code_path = tmp_path / "code.js"
    code_path.write_text(code, encoding="utf-8")
    runner_path = tmp_path / "runner.js"
    runner_path.write_text(RUNNER, encoding="utf-8")
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(item), encoding="utf-8")
    return subprocess.run([NODE, str(runner_path), str(code_path), str(input_path)],
                          capture_output=True, text=True, timeout=30, check=False)


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


def evaluate_expression(tmp_path, expression, item):
    """Evaluate an n8n expression such as '={{ ... }}' on one item, the way the Wait node reads it."""
    assert expression.startswith("={{") and expression.endswith("}}"), expression
    expr_path = tmp_path / "expr.js"
    expr_path.write_text(expression[3:-2].strip(), encoding="utf-8")
    runner_path = tmp_path / "expr-runner.js"
    runner_path.write_text(EXPRESSION_RUNNER, encoding="utf-8")
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(item), encoding="utf-8")
    result = subprocess.run([NODE, str(runner_path), str(expr_path), str(item_path)],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


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


def test_webhook_comes_from_environment_and_never_throws_on_4xx():
    for name in ("Post to Discord", "Retry once"):
        params = node_named(name)["parameters"]
        assert params["url"] == "={{ $env.HUNTER_DISCORD_WEBHOOK_URL }}"
        assert params["options"]["response"]["response"] == {"fullResponse": True, "neverError": True}


def test_429_branch_waits_retry_after_then_retries_once_without_loop():
    connections = load_workflow()["connections"]
    assert connections["Post to Discord"]["main"] == [[{"node": "Rate limited?", "type": "main", "index": 0}]]
    rate_limited = connections["Rate limited?"]["main"]
    assert rate_limited == [[{"node": "Wait for retry_after", "type": "main", "index": 0}],
                            [{"node": "Check delivery", "type": "main", "index": 0}]]
    assert connections["Wait for retry_after"]["main"] == [[{"node": "Retry once", "type": "main", "index": 0}]]
    # The retry goes only to the delivery check, which has no outgoing connection, so nothing loops back.
    assert connections["Retry once"]["main"] == [[{"node": "Check delivery", "type": "main", "index": 0}]]
    assert "Check delivery" not in connections
    condition = node_named("Rate limited?")["parameters"]["conditions"]["conditions"][0]
    assert condition["rightValue"] == 429
    assert "retry_after" in node_named("Wait for retry_after")["parameters"]["amount"]


def test_retry_resends_the_formatted_payload():
    body = node_named("Retry once")["parameters"]["jsonBody"]
    assert body == "={{ $('Format message').first().json }}"


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
    assert doc["connections"]["Error input"]["main"] == [[{"node": "Build failure message", "type": "main", "index": 0}]]
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


