"""Tests for scripts/check_workflows.py: each rule fires on its own bad fixture, and good input passes.

No live calls: every file read here is a fixture under tests/fixtures/ or the repository's own files.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_workflows as cw  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
ENV_EXAMPLE = cw.DEFAULT_ENV_EXAMPLE


def bad(name):
    return FIXTURES / "bad" / name


def rules_of(errors):
    """Rule ids from 'file: rule: detail' strings."""
    return {error.split(": ")[1] for error in errors}


def test_good_fixture_passes():
    assert cw.check_files([FIXTURES / "good"], ENV_EXAMPLE) == []


def test_empty_directory_passes(tmp_path):
    assert cw.check_files([tmp_path], ENV_EXAMPLE) == []


def test_real_workflows_pass():
    assert cw.check_files([cw.DEFAULT_WORKFLOWS], ENV_EXAMPLE) == []


def test_rule_missing_path(tmp_path):
    assert rules_of(cw.check_files([tmp_path / "absent.json"], ENV_EXAMPLE)) == {"missing-path"}


def test_rule_unreadable_file(tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root can read a chmod 000 file")
    path = tmp_path / "locked.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0)
    try:
        assert rules_of(cw.check_files([path], ENV_EXAMPLE)) == {"unreadable-file"}
    finally:
        path.chmod(0o644)


def test_rule_env_example_missing():
    errors = cw.check_files([FIXTURES / "good"], FIXTURES / "no-such.env")
    assert rules_of(errors) == {"env-example-missing"}


def test_rule_invalid_json():
    assert rules_of(cw.check_files([bad("invalid_json.json")], ENV_EXAMPLE)) == {"invalid-json"}


def test_rule_missing_field():
    assert rules_of(cw.check_files([bad("missing_keys.json")], ENV_EXAMPLE)) == {"missing-field"}


def test_rule_active_not_false():
    assert rules_of(cw.check_files([bad("active_true.json")], ENV_EXAMPLE)) == {"active-not-false"}


def test_rule_duplicate_workflow_id():
    pair = [bad("duplicate_id_a.json"), bad("duplicate_id_b.json")]
    assert rules_of(cw.check_files(pair, ENV_EXAMPLE)) == {"duplicate-workflow-id"}


def test_rule_duplicate_node_name():
    assert rules_of(cw.check_files([bad("duplicate_node_name.json")], ENV_EXAMPLE)) == {"duplicate-node-name"}


def test_rule_connection_shape():
    assert rules_of(cw.check_files([bad("connection_shape.json")], ENV_EXAMPLE)) == {"connection-shape"}


def test_rule_dangling_connection():
    assert rules_of(cw.check_files([bad("dangling_connection.json")], ENV_EXAMPLE)) == {"dangling-connection"}


def test_rule_secret_value_discord_webhook():
    errors = cw.check_files([bad("secret_discord_webhook.json")], ENV_EXAMPLE)
    assert rules_of(errors) == {"secret-value"}
    assert any("Discord webhook" in error for error in errors)


def test_rule_secret_value_pem():
    errors = cw.check_files([bad("secret_pem.json")], ENV_EXAMPLE)
    assert rules_of(errors) == {"secret-value"}
    assert any("PEM block" in error for error in errors)


def test_rule_secret_value_oci_fingerprint():
    errors = cw.check_files([bad("secret_oci_fingerprint.json")], ENV_EXAMPLE)
    assert rules_of(errors) == {"secret-value"}
    assert any("OCI key fingerprint" in error for error in errors)


def test_rule_secret_value_long_base64():
    errors = cw.check_files([bad("secret_long_base64.json")], ENV_EXAMPLE)
    assert rules_of(errors) == {"secret-value"}
    assert any("long base64 run" in error for error in errors)


def test_rule_secret_value_long_hex():
    errors = cw.check_files([bad("secret_long_hex.json")], ENV_EXAMPLE)
    assert rules_of(errors) == {"secret-value"}
    assert any("long hex run" in error for error in errors)


def test_rule_undeclared_env():
    assert rules_of(cw.check_files([bad("unknown_env.json")], ENV_EXAMPLE)) == {"undeclared-env"}


def test_rule_oracle_launch_without_cost_guard():
    errors = cw.check_files([bad("launch_without_cost_guard.json")], ENV_EXAMPLE)
    assert rules_of(errors) == {"oracle-launch-without-cost-guard"}


def test_rule_oracle_launch_bypasses_cost_guard():
    errors = cw.check_files([bad("launch_bypasses_cost_guard.json")], ENV_EXAMPLE)
    assert rules_of(errors) == {"oracle-launch-without-cost-guard"}


def test_rule_oracle_launch_bypasses_cost_guard_through_cycle():
    # Trigger and launch wired both ways: no node has zero incoming edges, yet the launch is still reachable.
    errors = cw.check_files([bad("launch_cycle_bypasses_cost_guard.json")], ENV_EXAMPLE)
    assert rules_of(errors) == {"oracle-launch-without-cost-guard"}
