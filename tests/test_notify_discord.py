"""Tests for workflows/notify-discord.json: the Format message code, run on recorded fixtures, and the wiring.

The Format message node runs under node on tests/fixtures/notify/*.input.json. Discord is never contacted.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / "workflows" / "notify-discord.json"
FIXTURES = ROOT / "tests" / "fixtures" / "notify"
NODE = shutil.which("node")

# Discord colours for each kind (decimal of 0x3498db, 0x2ecc71, 0xe74c3c).
BLUE, GREEN, RED = 3447003, 3066993, 15158332

# Runs the Code node body the way n8n does: $input.first() gives the item, the body returns the output items.
RUNNER = """
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');
const input = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const run = new Function('$input', code);
const out = run({ first: () => ({ json: input }) });
process.stdout.write(JSON.stringify(out[0].json));
"""


def load_workflow():
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


def node_named(name):
    return next(node for node in load_workflow()["nodes"] if node["name"] == name)


def run_format(tmp_path, item):
    """Run the Format message node on one input item. Returns the CompletedProcess."""
    code_path = tmp_path / "format.js"
    code_path.write_text(node_named("Format message")["parameters"]["jsCode"], encoding="utf-8")
    runner_path = tmp_path / "runner.js"
    runner_path.write_text(RUNNER, encoding="utf-8")
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(item), encoding="utf-8")
    return subprocess.run([NODE, str(runner_path), str(code_path), str(input_path)],
                          capture_output=True, text=True, timeout=30, check=False)


def format_message(tmp_path, item):
    result = run_format(tmp_path, item)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def fixture(name):
    return json.loads((FIXTURES / f"{name}.input.json").read_text(encoding="utf-8"))


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
    assert rate_limited == [[{"node": "Wait for retry_after", "type": "main", "index": 0}], []]
    assert connections["Wait for retry_after"]["main"] == [[{"node": "Retry once", "type": "main", "index": 0}]]
    # The retry has no outgoing connection, so it cannot loop back to the status check.
    assert "Retry once" not in connections
    condition = node_named("Rate limited?")["parameters"]["conditions"]["conditions"][0]
    assert condition["rightValue"] == 429
    assert "retry_after" in node_named("Wait for retry_after")["parameters"]["amount"]


def test_retry_resends_the_formatted_payload():
    body = node_named("Retry once")["parameters"]["jsonBody"]
    assert body == "={{ $('Format message').first().json }}"
