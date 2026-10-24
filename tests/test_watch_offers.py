"""Tests for the Parse and filter Code node of the watch-offers workflow, run under node on the offers fixtures.

The node reads the free-for-dev README (tests/fixtures/offers/free-for-dev.md) and the LowEndBox items (lowendbox.xml),
with the seen map, the day and the counters in its static data and the clock fixed at NOW. Nothing is fetched and no
network call is made.
"""
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / "workflows" / "watch-offers.json"
FIXTURES = ROOT / "tests" / "fixtures" / "offers"
NODE = shutil.which("node")
PARSE = "Parse and filter"
RETRY = "Retry delay"

NOW = "2026-10-24T12:00:00Z"  # the clock the node sees; noon UTC, so the day is 2026-10-24 in every zone checked here
STAMP = "2026-10-24T12:00:00.000Z"  # the same instant as the node writes it (toISOString)
DAY = "2026-10-24"
LOWEND = "https://lowendbox.example.test/deals/"

# The six offers under the wanted headings (Web Hosting, IaaS) of the fixture, in README order.
WANTED_OFFERS = [
    ("Example Pages", "https://pages.example.test"),
    ("Sample Compute", "https://compute.example.test"),
    ("Demo Containers", "https://containers.example.test"),
    ("Example Cloud", "https://cloud.example.test"),
    ("Sample VPS", "https://vps.example.test"),
    ("Tiny Box", "https://tinybox.example.test"),
]
UNWANTED_URLS = [
    "https://db.example.test", "https://cache.example.test", "https://mail.example.test",
    "https://metrics.example.test", "https://arcade.example.test",
]
PAID_FEED = [{"title": "Paid VPS", "link": "https://paid.example.test/vps", "description": "$5 per month"}]

# Runs the Parse and filter body the way n8n does: $input.all() gives the feed items, $env the environment, $('Name') an
# earlier node's output, $getWorkflowStaticData the static data, and Date is fixed at ctx.now. Prints the items the node
# returned and the static data it left.
RUNNER = """
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');
const ctx = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const FixedDate = class extends Date {
  constructor(...args) { super(...(args.length ? args : [ctx.now])); }
  static now() { return Date.parse(ctx.now); }
};
const $ = (name) => ({ first: () => ({ json: ctx.nodes?.[name] ?? null }) });
const staticData = ctx.staticData ?? {};
const feed = (ctx.items ?? []).map((json) => ({ json }));
const out = new Function('$input', '$env', '$', '$getWorkflowStaticData', 'Date', code)(
  { all: () => feed, first: () => feed[0] ?? { json: null } }, ctx.env ?? {}, $, () => staticData, FixedDate);
process.stdout.write(JSON.stringify({ items: out.map((item) => item.json), staticData }));
"""


def node_code(name):
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    return next(node for node in workflow["nodes"] if node["name"] == name)["parameters"]["jsCode"]


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def lowendbox_items():
    """The LowEndBox fixture as the RSS Read node emits it: one item per post."""
    root = ET.parse(FIXTURES / "lowendbox.xml").getroot()
    return [{"title": item.findtext("title"), "link": item.findtext("link"), "description": item.findtext("description")}
            for item in root.iter("item")]


def fetched(markdown=None):
    """The Fetch free-for-dev node's output: a 200 with the README text (the recorded fixture unless markdown is given)."""
    return {"statusCode": 200, "body": fixture_text("free-for-dev.md") if markdown is None else markdown, "headers": {}}


def offer_line(name, url, source="free-for-dev"):
    return f"• {name} ({source}): {url}"


def run_watch(tmp_path, items, readme, static_data, now=NOW, node=PARSE):
    """Run one Code node of the workflow once. Returns (items it emitted, static data it left)."""
    code_path = tmp_path / "node.js"
    code_path.write_text(node_code(node), encoding="utf-8")
    runner_path = tmp_path / "runner.js"
    runner_path.write_text(RUNNER, encoding="utf-8")
    context = {"items": items, "nodes": {"Fetch free-for-dev": readme}, "staticData": static_data, "now": now}
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    result = subprocess.run([NODE, str(runner_path), str(code_path), str(context_path)],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    return out["items"], out["staticData"]


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_first_run_records_every_parsed_offer_and_reports_nothing(tmp_path):
    items, static = run_watch(tmp_path, lowendbox_items(), fetched(), {})
    assert items == []
    assert set(static["seen"]) >= {url for _, url in WANTED_OFFERS}
    assert static["metrics"]["counters"]["new_offers"] == 0


@needs_node
def test_parses_the_wanted_sections_of_the_fixture(tmp_path):
    _, static = run_watch(tmp_path, PAID_FEED, fetched(), {})
    assert set(static["seen"]) == {url for _, url in WANTED_OFFERS}
    assert static["metrics"]["counters"]["fetched"] == {"free-for-dev": 6, "lowendbox": 1}


@needs_node
def test_offers_are_listed_in_readme_order_with_name_source_and_link(tmp_path):
    items, _ = run_watch(tmp_path, PAID_FEED, fetched(), {"seen": {}})
    assert items == [{"kind": "offer", "title": "6 new free offers", "url": "",
                      "body": "\n".join(offer_line(name, url) for name, url in WANTED_OFFERS)}]


@needs_node
def test_unwanted_headings_produce_no_offers(tmp_path):
    _, static = run_watch(tmp_path, PAID_FEED, fetched(), {})
    assert not set(static["seen"]) & set(UNWANTED_URLS)


@needs_node
def test_a_readme_with_only_unwanted_headings_yields_no_offers_but_an_alert(tmp_path):
    markdown = ("## Databases\n\n  * [Example DB](https://db.example.test) — free tier\n\n"
                "## Unwanted: Games\n\n  * [Example Arcade](https://arcade.example.test) — free to play\n")
    items, static = run_watch(tmp_path, PAID_FEED, fetched(markdown), {"seen": {}})
    assert [item["kind"] for item in items] == ["alert"]
    assert items[0]["title"] == "No offers parsed: free-for-dev"
    assert static["seen"] == {}


@needs_node
def test_free_lowendbox_posts_are_kept(tmp_path):
    _, static = run_watch(tmp_path, lowendbox_items(), fetched(), {})
    for slug in ["free-vps-1gb", "kvm-frankfurt", "free-tier-storage", "free-ipv4", "ryzen-trial"]:
        assert LOWEND + slug in static["seen"], slug


@needs_node
def test_paid_lowendbox_posts_are_dropped(tmp_path):
    _, static = run_watch(tmp_path, lowendbox_items(), fetched(), {})
    for slug in ["nvme-budget", "black-friday-xen"]:
        assert LOWEND + slug not in static["seen"], slug


@needs_node
def test_free_keyword_match_ignores_case_and_checks_the_summary_too(tmp_path):
    items = [
        {"title": "FREE tier VPS", "link": "https://case.example.test/a", "description": ""},
        {"title": "Setup $0 VPS", "link": "https://case.example.test/b", "description": ""},
        {"title": "Plan", "link": "https://case.example.test/c", "description": "ALWAYS FREE for hobby use"},
    ]
    _, static = run_watch(tmp_path, items, fetched(), {})
    assert {"https://case.example.test/a", "https://case.example.test/b", "https://case.example.test/c"} <= set(static["seen"])
    assert static["metrics"]["counters"]["filtered_out"] == 0


@needs_node
def test_paid_posts_are_counted_as_filtered_out(tmp_path):
    items = [{"title": "Free VPS", "link": "https://count.example.test/free", "description": ""}] + [
        {"title": "Cheap VPS", "link": f"https://count.example.test/paid{n}", "description": "$3 per month"}
        for n in (1, 2)]
    _, static = run_watch(tmp_path, items, fetched(), {})
    assert static["metrics"]["counters"]["filtered_out"] == 2
    assert static["metrics"]["counters"]["fetched"]["lowendbox"] == 3


@needs_node
def test_paid_post_that_says_no_free_tier_is_dropped(tmp_path):
    openvz = [item for item in lowendbox_items() if item["link"].endswith("openvz-cheap")]
    _, static = run_watch(tmp_path, openvz, fetched(), {})
    assert LOWEND + "openvz-cheap" not in static["seen"]


@needs_node
def test_only_offers_not_yet_seen_are_reported(tmp_path):
    seen = {"https://pages.example.test": "2026-10-23T08:00:00.000Z"}
    items, static = run_watch(tmp_path, PAID_FEED, fetched(), {"seen": seen})
    assert items == [{"kind": "offer", "title": "5 new free offers", "url": "",
                      "body": "\n".join(offer_line(name, url) for name, url in WANTED_OFFERS[1:])}]
    assert static["seen"]["https://pages.example.test"] == "2026-10-23T08:00:00.000Z"
    assert static["metrics"]["counters"]["new_offers"] == 5
    assert static["metrics"]["counters"]["skipped_seen"] == 1


@needs_node
def test_a_second_run_with_the_same_feed_reports_nothing(tmp_path):
    items, static = run_watch(tmp_path, PAID_FEED, fetched(), {})
    assert items == []
    items, static = run_watch(tmp_path, PAID_FEED, fetched(), static)
    assert items == []
    assert static["metrics"]["counters"]["skipped_seen"] == 6


@needs_node
def test_seen_entries_older_than_180_days_are_dropped(tmp_path):
    old = "2026-04-26T12:00:00.000Z"  # 181 days before NOW
    recent = "2026-04-28T12:00:00.000Z"  # 179 days before NOW
    seen = {"https://gone.example.test": old, "https://kept.example.test": recent}
    _, static = run_watch(tmp_path, PAID_FEED, fetched(), {"seen": seen})
    assert "https://gone.example.test" not in static["seen"]
    assert static["seen"]["https://kept.example.test"] == recent


@needs_node
def test_at_most_ten_offers_are_listed_and_the_rest_counted(tmp_path):
    markdown = "## Web Hosting\n\n" + "".join(
        f"  * [Host {n}](https://host{n}.example.test) — free tier\n" for n in range(1, 13))
    items, _ = run_watch(tmp_path, PAID_FEED, fetched(markdown), {"seen": {}})
    assert len(items) == 1
    assert items[0]["title"] == "12 new free offers"
    lines = items[0]["body"].split("\n")
    assert len(lines) == 11
    assert lines[-1] == "and 2 more"


@needs_node
def test_counters_count_the_run_and_add_up_within_the_day(tmp_path):
    metrics = {"day": DAY, "counters": {"runs": 4, "new_offers": 10, "skipped_seen": 2, "filtered_out": 1, "errors": 0},
               "lastSuccessAt": {}, "alerted": {}}
    _, static = run_watch(tmp_path, PAID_FEED, fetched(), {"seen": {"https://pages.example.test": STAMP}, "metrics": metrics})
    assert static["metrics"]["counters"] == {
        "runs": 5, "new_offers": 15, "skipped_seen": 3, "filtered_out": 2, "errors": 0,
        "fetched": {"free-for-dev": 6, "lowendbox": 1},
    }


@needs_node
def test_a_new_day_keeps_the_counters_for_the_summary_and_resets_the_alerts(tmp_path):
    metrics = {"day": "2026-10-23", "counters": {"runs": 3}, "lastSuccessAt": {}, "alerted": {"fetch:lowendbox": True}}
    _, static = run_watch(tmp_path, PAID_FEED, fetched(), {"seen": {}, "metrics": metrics})
    assert static["metrics"]["counters"]["runs"] == 4
    assert static["metrics"]["alerted"] == {}


@needs_node
def test_failed_readme_counts_an_error_and_alerts_once_a_day(tmp_path):
    failed = {"statusCode": 503, "body": "", "headers": {}}
    recent = {"seen": {}, "metrics": {"day": DAY, "counters": {}, "alerted": {},
                                      "lastSuccessAt": {"free-for-dev": STAMP, "lowendbox": STAMP}}}
    items, static = run_watch(tmp_path, PAID_FEED, failed, recent)
    assert items == [{"kind": "alert", "title": "Feed failed: free-for-dev", "url": "",
                      "body": "HTTP 503 from the free-for-dev README. Next try in 6 hours."}]
    assert static["metrics"]["counters"]["errors"] == 1
    again, _ = run_watch(tmp_path, PAID_FEED, failed, static)
    assert again == []


@needs_node
def test_a_failed_source_with_no_success_for_12_hours_is_reported_as_stale(tmp_path):
    static = {"seen": {}, "metrics": {"day": DAY, "counters": {}, "alerted": {},
                                      "lastSuccessAt": {"free-for-dev": "2026-10-23T23:59:00.000Z", "lowendbox": STAMP}}}
    items, _ = run_watch(tmp_path, PAID_FEED, {"statusCode": 503, "body": "", "headers": {}}, static)
    assert [item["title"] for item in items] == ["Feed failed: free-for-dev", "Stale source: free-for-dev"]


@needs_node
def test_a_source_read_within_12_hours_is_not_stale(tmp_path):
    static = {"seen": {}, "metrics": {"day": DAY, "counters": {}, "alerted": {},
                                      "lastSuccessAt": {"free-for-dev": "2026-10-24T00:01:00.000Z", "lowendbox": STAMP}}}
    items, _ = run_watch(tmp_path, PAID_FEED, {"statusCode": 503, "body": "", "headers": {}}, static)
    assert [item["title"] for item in items] == ["Feed failed: free-for-dev"]


@needs_node
def test_a_seen_list_over_5000_entries_raises_one_alert_a_day(tmp_path):
    seen = {f"https://old{n}.example.test": STAMP for n in range(5000)}
    items, static = run_watch(tmp_path, PAID_FEED, fetched(), {"seen": seen})
    alerts = [item for item in items if item["kind"] == "alert"]
    assert [item["title"] for item in alerts] == ["Seen list is large"]
    assert static["metrics"]["alerted"]["seen:size"] is True


@needs_node
def test_retry_after_seconds_are_waited_in_full(tmp_path):
    items, _ = run_watch(tmp_path, [{"headers": {"retry-after": "1200"}}], {}, {}, node=RETRY)
    assert items == [{"seconds": 1200}]


@needs_node
def test_retry_after_is_never_shorter_than_60_seconds(tmp_path):
    for header in ["0", "30", None]:
        headers = {} if header is None else {"retry-after": header}
        items, _ = run_watch(tmp_path, [{"headers": headers}], {}, {}, node=RETRY)
        assert items == [{"seconds": 60}], header


@needs_node
def test_retry_after_http_date_is_the_seconds_until_that_time(tmp_path):
    items, _ = run_watch(tmp_path, [{"headers": {"retry-after": "Sat, 24 Oct 2026 12:20:00 GMT"}}], {}, {}, node=RETRY)
    assert items == [{"seconds": 1200}]


def test_every_notify_call_sends_one_item_per_run():
    # notify-discord sends only its first input item, so several alerts in one run need one call each.
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    calls = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.executeWorkflow"]
    assert calls
    assert all(node["parameters"].get("mode") == "each" for node in calls)
